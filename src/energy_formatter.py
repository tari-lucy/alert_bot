"""
Сборка готового текста поста из провалидированных данных извлечения
(см. llm_extractor) + блока адресов, взятого ДОСЛОВНО из исходного поста.

Текст собирается кодом, а не LLM — гарантируется корректность формата,
времени и адресов. Адреса не переписываются и не берутся из локальных
списков: Севастопольэнерго меняет их от поста к посту, поэтому единственный
достоверный источник — сам пост. Блок вырезается как подстрока оригинала,
так что сгенерированный моделью текст в публикацию не попадает.

Если формат поста изменится и вырезать адреса не удастся — публикуем
очередь и время без адресов (лучше меньше данных, чем неверные).
Один пост = один текст (без картинок), публикуется как в TG, так и в MAX
через общий publish_alert_template.
"""
import html
import re
from .logger import setup_logger

logger = setup_logger('energy_formatter')

# Реклама MAX-канала самого источника и жалобы на сбои Telegram — вырезаем
# из дословного текста: мы публикуем в свои каналы.
_AD_RE = re.compile(
    r'max\.ru'
    r'|наблюдаются\s+сбои'
    r'|наш\w*\s+канал\w*\s+(?:в\s+)?(?:МАКС|МАХ)',
    re.I | re.U
)

# Боилерплейт-приписки после адресов — общие слова без конкретики, повторяются
# из поста в пост. Их убираем. Но СОДЕРЖАТЕЛЬНЫЕ хвостовые фразы («по возможности
# включим часть потребителей Гагаринского района») сохраняем — это важно людям.
_BOILERPLATE_RE = re.compile(
    r'делают\s+всё\s+возможное'
    r'|минимизировать\s+неудобств'
    r'|наблюдаются\s+сбои'
    r'|max\.ru'
    r'|наш\w*\s+канал',
    re.I | re.U
)

# Сноска самого Севастопольэнерго — срезаем из блока и ставим уже своей строкой
_FOOTER_RE = re.compile(r'\n?\s*\*\s*Список\s+адресов.*$', re.S | re.I)

# Объявление очереди («по графику 1 очереди», «у части потребителей 2 очереди»,
# «1-й очереди», «2 очередь»). Захватывает номер; конец совпадения — сразу
# после слова «очередь», что позволяет забрать адреса и с той же строки.
_QUEUE_DECL_RE = re.compile(r'(\d)\s*-?\s*(?:[а-я]{1,3}\s*)?очеред(?:и|ь|ей|ью)?', re.I)

# Признаки того, что абзац — действительно адреса, а не хвостовая проза
# («Энергетики делают всё возможное…», «По возможности включим…»). Список
# намеренно широкий, включая нежилые объекты, чтобы не срезать реальную строку.
# Аббревиатуры-объекты — только как отдельные слова (\b), иначе «СК» ловит
# «Гагарин[ск]ого», «АО» — «[ао]» внутри слов, и хвостовая проза не отсекается.
_ADDRESS_MARKER_RE = re.compile(
    r'(ул\.|ул\s|пос\.|просп|пр\.|пр-кт|пр-д|пер\.|наб\.|пл\.|\bс\.|\bг\.|\bп\.|\bх\.|хут|'
    r'шоссе|бухта|балка|мыс|\bкм\b|бульв|причал|завод|з-д|'
    r'\bЖК\b|\bЖСК\b|\bЖСТ\b|\bЖЗС\b|\bСНТ\b|\bСТ\b|\bСК\b|\bООО\b|\bАО\b|\bЗАО\b|\bИП\b|\bТЦ\b)', re.I
)

_ADDRESSES_NOTE = "<i>*Список адресов может быть неполным.</i>"

# Лимит сообщения. MAX = 4000, Telegram = 4096; берём строгий, тогда влезает в оба.
MAX_MESSAGE_LEN = 4000

# Потолок числа частей на один пост. Реальные посты укладываются в 1-2 части;
# ограничение — страховка от аномально длинных «простыней», чтобы не слать
# десяток сообщений подряд. При переполнении хвост адресов заменяется ссылкой
# на первоисточник.
MAX_PARTS = 3

_BLANK_LINE_RE = re.compile(r'\n\s*\n')

# Зачин списка адресов в постах БЕЗ номера очереди: «...обесточены
# потребители:», «...электроэнергия отсутствует по адресам:».
_ADDR_LEADIN_RE = re.compile(r'(?:по\s+адрес\w*|потребител\w*|адрес\w*)\s*:', re.I | re.U)

# «у ЧАСТИ потребителей N очереди» — затрагивается не вся очередь, а её часть.
# Разница смысловая: человек из этой очереди иначе решит, что свет будет
# (или пропадёт) у него. Ищем в тексте, а не спрашиваем модель, — так «часть»
# нельзя ни потерять, ни выдумать.
_PARTIAL_QUEUE_RE = re.compile(
    r'част\w*\s+потребител\w*\s+(\d)\s*-?\s*[а-я]{0,2}\s*очеред', re.I | re.U
)


def is_partial_queue(post_text: str, queue) -> bool:
    """Сказано ли в посте «у части потребителей N очереди» для этой очереди."""
    if not post_text or queue is None:
        return False
    return any(
        m.group(1) == str(queue) for m in _PARTIAL_QUEUE_RE.finditer(post_text)
    )


def _segments_by_queue(post_text: str) -> dict:
    """
    Режет пост на блоки адресов по объявлениям очереди.

    Возвращает {номер_очереди: [блок, ...]} — блоки дословные подстроки поста.
    Для каждого объявления берём адреса до строки следующего объявления и
    подхватываем оба формата: адреса на следующей строке и на той же строке.
    """
    body = _FOOTER_RE.sub('', post_text)
    matches = list(_QUEUE_DECL_RE.finditer(body))
    if not matches:
        return {}

    segments = {}
    for i, m in enumerate(matches):
        word_end = m.end()
        # Граница блока — начало строки со следующим объявлением (чтобы не
        # захватить его временную преамбулу «С 18:00 до 21:00 по графику …»).
        if i + 1 < len(matches):
            nxt = body.rfind('\n', 0, matches[i + 1].start()) + 1
        else:
            nxt = len(body)
        if nxt <= word_end:
            continue

        # Основной вариант: адреса на отдельной строке после объявления.
        line_end = body.find('\n', word_end)
        primary = body[line_end:nxt].strip() if 0 <= line_end < nxt else ''
        # Запасной: адреса на той же строке сразу после слова «очередь».
        secondary = body[word_end:nxt].lstrip(' \t:.,;—–-').strip()

        block = primary if _ADDRESS_MARKER_RE.search(primary) else secondary
        if not block:
            continue
        try:
            queue = int(m.group(1))
        except (TypeError, ValueError):
            continue
        segments.setdefault(queue, []).append(block)
    return segments


def _raw_block(post_text: str, queue):
    """
    Сырой блок «адреса + возможный хвост» из поста или None.

    queue=None — пост без очереди: якорь по зачину списка («…потребители:»,
    «…по адресам:»), берём всё после последнего зачина. Иначе — по объявлению
    очереди; None, если очередь объявлена несколько раз (привязка неоднозначна).
    """
    if queue is None:
        body = _FOOTER_RE.sub('', post_text)
        last = None
        for last in _ADDR_LEADIN_RE.finditer(body):
            pass
        if last is None:
            return None
        return body[last.end():].strip() or None

    blocks = _segments_by_queue(post_text).get(queue) or []
    if len(blocks) != 1:
        if len(blocks) > 1:
            logger.warning(
                f"Очередь {queue} объявлена в посте {len(blocks)} раза — "
                "привязка адресов неоднозначна, публикуем без адресов"
            )
        return None
    return blocks[0]


def _partition_block(block: str):
    """
    Делит блок на (адреса, хвост).

    Адреса — абзацы до последнего похожего на адрес включительно (внутренние
    неадресные абзацы остаются с адресами). Хвост — то, что после них: общие
    фразы источника. Из хвоста выкидываем боилерплейт («делают всё возможное…»,
    реклама MAX), но СОДЕРЖАТЕЛЬНЫЕ фразы («по возможности включим …район»)
    сохраняем — это важно людям.
    """
    paragraphs = _split_paragraphs(block)
    marker_idx = [i for i, p in enumerate(paragraphs) if _ADDRESS_MARKER_RE.search(p)]
    if not marker_idx:
        return '', ''
    cut = marker_idx[-1] + 1
    addresses = "\n\n".join(paragraphs[:cut])
    tail = "\n\n".join(p for p in paragraphs[cut:] if not _BOILERPLATE_RE.search(p))
    return addresses, tail


def extract_addresses(post_text: str, queue: int = None):
    """Дословный блок адресов или None (хвост-проза отсечена)."""
    if not post_text:
        return None
    block = _raw_block(post_text, queue)
    if not block:
        return None
    addresses, _ = _partition_block(block)
    if len(addresses) < 20 or not _ADDRESS_MARKER_RE.search(addresses):
        if queue is not None:
            logger.warning(f"Блок после объявления очереди {queue} не похож на адреса — публикуем без них")
        return None
    return addresses


def extract_tail(post_text: str, queue: int = None) -> str:
    """Содержательный хвост после адресов (без боилерплейта) или ''."""
    if not post_text:
        return ''
    block = _raw_block(post_text, queue)
    if not block:
        return ''
    _, tail = _partition_block(block)
    return tail


def _split_paragraphs(block: str) -> list:
    """
    Делит блок адресов на абзацы по разметке самого источника.

    Если автор поста разделил секции пустыми строками («Ленинский район:… /
    Инфраструктура:… / Гагаринский район:…»), то абзацы — это они, а одиночные
    переносы внутри секции — ручные переносы строк, их схлопываем в один поток.
    Если пустых строк нет (старый формат) — каждая строка это своя группа
    адресов и становится отдельным абзацем.
    """
    if _BLANK_LINE_RE.search(block):
        chunks = _BLANK_LINE_RE.split(block)
        paragraphs = [
            ' '.join(ln.strip() for ln in chunk.split('\n') if ln.strip())
            for chunk in chunks
        ]
    else:
        paragraphs = [ln.strip() for ln in block.split('\n')]
    return [p for p in paragraphs if p]


def _clean_body(post_text: str) -> str:
    """
    Дословный текст поста, приведённый в порядок: без рекламы MAX-канала
    источника, без служебной сноски про адреса, разбитый на абзацы.

    Слова источника не меняются — только выброшены рекламные абзацы.
    """
    body = _FOOTER_RE.sub('', post_text or '')
    paragraphs = [p for p in _split_paragraphs(body) if not _AD_RE.search(p)]
    return "\n\n".join(html.escape(p, quote=False) for p in paragraphs)


def _prepare_addresses(block: str) -> str:
    """
    Готовит блок адресов к публикации: абзацы + экранирование + курсив.

    Сами адреса не переписываются — меняются только переносы строк. Текст
    экранируется (публикуем с HTML-разметкой), каждый абзац отдельно
    оборачивается в курсив — так одинаково рендерится и в TG, и в MAX.
    """
    return "\n\n".join(
        f"<i>{html.escape(p, quote=False)}</i>" for p in _split_paragraphs(block)
    )


def _prepare_tail(tail: str) -> str:
    """Содержательный хвост — обычным текстом (не курсивом, это не адреса)."""
    return "\n\n".join(html.escape(p, quote=False) for p in _split_paragraphs(tail))


def _boundary_len(text: str, n: int) -> int:
    """Длина префикса ≤ n, обрезанного по последней запятой/переносу (адрес цел)."""
    cut = text[:n]
    b = max(cut.rfind(','), cut.rfind('\n'))
    return (b + 1) if b > 0 else n


def _split_address_chunks(fixed_lines: list, addresses: str) -> list:
    """
    Режет блок адресов на куски так, чтобы каждый кусок В СОБРАННОМ виде
    влезал в лимит сообщения. Ни один адрес не теряется — длинные простыни
    публикуются несколькими частями.

    fixed_lines — заведомо худший набор служебных строк (шапка + метка части +
    время + источник + подзаголовок + сноска), чтобы бюджет куска был с запасом
    для любой реальной части. Границу куска ищем по ФАКТИЧЕСКИ отрендеренной
    длине: разметка (<i> на абзац) и экранирование меняют длину непредсказуемо.
    """
    def fits(chunk: str) -> bool:
        rendered = "\n".join(fixed_lines + ["", _prepare_addresses(chunk), "", _ADDRESSES_NOTE])
        return len(rendered) <= MAX_MESSAGE_LEN

    chunks = []
    remaining = addresses.strip()
    while remaining:
        if fits(remaining):
            chunks.append(remaining)
            break
        lo, hi, best_n = 1, len(remaining), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            blen = _boundary_len(remaining, mid)
            if blen and fits(remaining[:blen].rstrip(' ,\n')):
                best_n = blen
                lo = mid + 1
            else:
                hi = mid - 1
        if best_n == 0:
            # Даже минимальный кусок не влезает (аномально длинный адрес без
            # запятых) — берём по границе, чтобы не зациклиться и не потерять.
            best_n = _boundary_len(remaining, len(remaining))
        chunks.append(remaining[:best_n].rstrip(' ,\n'))
        remaining = remaining[best_n:].lstrip(' ,\n')
    return chunks


class EnergyFormatter:
    def __init__(self, source_name: str = ''):
        """
        Args:
            source_name: подпись источника (напр. "Севастопольэнерго")
        """
        self.source_name = source_name

    def _source_line(self) -> str:
        if self.source_name:
            return f'<i>Об этом сообщило «{self.source_name}».</i>'
        return ''

    def _source_pointer(self) -> str:
        """Ссылка на первоисточник за полным списком (для очень длинных постов)."""
        if self.source_name:
            return f'<i>Полный список адресов — в канале «{self.source_name}».</i>'
        return '<i>Полный список адресов — в первоисточнике.</i>'

    def _time_line(self, time_from, time_to, restore=None):
        """
        Строка времени из того, что реально написано в посте.

        Ничего не додумываем: нет времени вовсе — строки не будет.
        «restore» — это «ориентировочное время восстановления», оно не равно
        концу интервала, поэтому и формулировка другая.
        """
        if time_from and time_to:
            line = f"🕐 {time_from}–{time_to}"
        elif time_to:
            line = f"🕐 До {time_to}"
        elif time_from:
            line = f"🕐 С {time_from}"
        else:
            line = None

        if restore:
            if line:
                return f"{line}, восстановление ориентировочно в {restore}"
            return f"🕐 Восстановление ориентировочно в {restore}"
        return line

    def _format_window(self, header: str, addresses_title: str, queue,
                       time_from, time_to, restore=None, addresses: str = None,
                       tail: str = '') -> list:
        """Список готовых сообщений. Длинные адреса — несколькими частями."""
        time_line = self._time_line(time_from, time_to, restore)
        source = self._source_line()
        tail_block = _prepare_tail(tail) if tail else ''

        if not addresses:
            parts = [header]
            if time_line:
                parts += ["", time_line]
            if source:
                parts += ["", source]
            if tail_block:
                parts += ["", tail_block]
            return ["\n".join(parts)]

        # Очередь в подзаголовке — только если она в посте реально названа
        suffix = f" ({queue} очередь)" if queue else ""

        # Худший набор служебных строк для бюджета куска: шапка с САМОЙ ШИРОКОЙ
        # меткой части (двузначные/трёхзначные номера дают 1-2 лишних символа),
        # время, источник, подзаголовок-продолжение, сноска и хвост (всё это
        # окажется на последней части). Любая реальная часть короче — влезет.
        worst_lines = [f"{header}  (часть 999/999)"]
        if time_line:
            worst_lines += ["", time_line]
        if source:
            worst_lines += ["", source]
        worst_lines += ["", f"<b>{addresses_title}{suffix} (продолжение):</b>"]
        if tail_block:
            worst_lines += ["", tail_block]

        pointer = self._source_pointer()
        chunks = _split_address_chunks(worst_lines, addresses)
        overflow = len(chunks) > MAX_PARTS
        if overflow:
            # Пост аномально длинный: режем до MAX_PARTS, зарезервировав на
            # последней части место под ссылку на первоисточник.
            chunks = _split_address_chunks(worst_lines + ["", pointer], addresses)[:MAX_PARTS]
            logger.warning(
                f"Пост длиннее {MAX_PARTS} частей — публикуем {MAX_PARTS}, "
                "остальные адреса — ссылкой на первоисточник"
            )
        elif len(chunks) > 1:
            logger.info(f"Адреса не влезают в одно сообщение — публикуем {len(chunks)} частями")

        total = len(chunks)
        messages = []
        for i, chunk in enumerate(chunks, 1):
            label = f"  (часть {i}/{total})" if total > 1 else ""
            parts = [f"{header}{label}"]
            if i == 1:
                # Время и источник — только на первой части
                if time_line:
                    parts += ["", time_line]
                if source:
                    parts += ["", source]
            cont = " (продолжение)" if i > 1 else ""
            parts += ["", f"<b>{addresses_title}{suffix}{cont}:</b>", "", _prepare_addresses(chunk)]
            if i == total:
                # Хвост последней части: содержательная приписка источника (если
                # была), затем сноска, а при переполнении — ссылка на первоисточник.
                if tail_block:
                    parts += ["", tail_block]
                parts += ["", _ADDRESSES_NOTE]
                if overflow and pointer:
                    parts += ["", pointer]
            messages.append("\n".join(parts))
        return messages

    def _queue_suffix(self, queue, partial: bool) -> str:
        """
        Хвост заголовка про очередь.

        «у части N очереди» против «N очередь» — разница принципиальная:
        во втором случае читатель из этой очереди ждёт свет у себя.
        """
        if not queue:
            return ""
        if partial:
            return f" — у части {queue} очереди"
        return f" — {queue} очередь"

    def format_outage(self, queue, time_from, time_to, confirmed: bool,
                      restore=None, addresses: str = None, partial: bool = False,
                      tail: str = '') -> list:
        """Отключение в рамках ГВО. queue=None — отключение без номера очереди."""
        suffix = self._queue_suffix(queue, partial)
        if confirmed:
            header = f"🔌 <b>Отключение электроэнергии{suffix}</b>"
        else:
            header = f"⚠️ <b>Ожидается отключение электроэнергии{suffix}</b>"
        return self._format_window(
            header, "Адреса отключения", queue, time_from, time_to, restore, addresses, tail
        )

    def format_supply(self, queue, time_from, time_to, restore=None,
                      addresses: str = None, partial: bool = False, tail: str = '') -> list:
        """
        Пост «где свет БУДЕТ» — антипод отключения.

        Источник пишет «ориентировочно будет», гарантии он не даёт, поэтому
        и заголовок не обещает твёрдо.
        """
        suffix = self._queue_suffix(queue, partial)
        header = f"💡 <b>Ориентировочно будет свет{suffix}</b>"
        return self._format_window(
            header, "Адреса", queue, time_from, time_to, restore, addresses, tail
        )

    def _format_verbatim(self, header: str, post_text: str) -> str:
        """
        Заголовок наш, тело — дословный текст источника, приведённый в порядок.

        Пересказывать такие посты нечем и незачем: ценность в самом объяснении
        энергетиков, а дословность исключает искажение. Вычищаем только рекламу
        их MAX-канала и служебные сноски.
        """
        parts = [header]
        source = self._source_line()
        if source:
            parts += ["", source]

        body = _clean_body(post_text)
        if body:
            parts += ["", body]
        return "\n".join(parts)

    def format_lifted(self, post_text: str = '') -> str:
        return self._format_verbatim(
            "✅ <b>Электроснабжение восстанавливается</b>", post_text
        )

    def format_regime(self, schedule: str, post_text: str = '') -> str:
        """Объявление о самом графике ГВО («2 через 6»)."""
        suffix = f" — «{html.escape(schedule, quote=False)}»" if schedule else ""
        return self._format_verbatim(
            f"⚙️ <b>Изменён график отключений{suffix}</b>", post_text
        )

    def build_messages(self, extraction: dict, post_text: str = '') -> list:
        """
        Список готовых текстов для публикации. Для outage с несколькими окнами — несколько.

        post_text — исходный пост канала: из него дословно берутся адреса.
        """
        msg_type = extraction.get('type')
        if msg_type == 'lifted':
            return [self.format_lifted(post_text)]
        if msg_type == 'regime':
            return [self.format_regime(extraction.get('schedule'), post_text)]
        if msg_type not in ('outage', 'supply'):
            return []

        confirmed = extraction.get('confirmed', False)
        messages = []
        for w in extraction.get('windows', []):
            queue = w.get('queue')
            addresses = extract_addresses(post_text, queue)
            if not addresses:
                logger.warning(
                    f"Адреса не извлечены (очередь: {queue or 'не указана'}) — пост без адресов"
                )
            partial = is_partial_queue(post_text, queue)
            # Содержательная приписка источника после адресов («по возможности
            # включим …район») — сохраняем; лежит в блоке того окна, за которым
            # физически идёт, поэтому берём её per-window.
            tail = extract_tail(post_text, queue)
            if msg_type == 'supply':
                messages.extend(
                    self.format_supply(queue, w.get('from'), w.get('to'),
                                       w.get('restore'), addresses, partial, tail)
                )
            else:
                messages.extend(
                    self.format_outage(queue, w.get('from'), w.get('to'), confirmed,
                                       w.get('restore'), addresses, partial, tail)
                )
        return messages
