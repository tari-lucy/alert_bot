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

# Сноска самого Севастопольэнерго — срезаем из блока и ставим уже своей строкой
_FOOTER_RE = re.compile(r'\n?\s*\*\s*Список\s+адресов.*$', re.S | re.I)

# Объявление очереди («по графику 1 очереди», «у части потребителей 2 очереди»,
# «1-й очереди», «2 очередь»). Захватывает номер; конец совпадения — сразу
# после слова «очередь», что позволяет забрать адреса и с той же строки.
_QUEUE_DECL_RE = re.compile(r'(\d)\s*-?\s*(?:[а-я]{1,3}\s*)?очеред(?:и|ь|ей|ью)?', re.I)

# Признаки того, что вырезанный блок — действительно адреса, а не проза
_ADDRESS_MARKER_RE = re.compile(
    r'(ул\.|ул\s|пос\.|просп|пр\.|пер\.|наб\.|пл\.|с\.|г\.|п\.|х\.|хут|'
    r'шоссе|бухта|балка|мыс|\bкм\b|бульв|ЖК|ЖСК|СК|ООО|АО)', re.I
)

_ADDRESSES_NOTE = "<i>*Список адресов может быть неполным.</i>"

# Запас под лимит сообщения (Telegram — 4096, MAX — свой; берём с запасом)
MAX_MESSAGE_LEN = 4000

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


def _addresses_without_queue(post_text: str):
    """
    Блок адресов для поста БЕЗ номера очереди (по району или по дефициту).

    Якорь — зачин списка («обесточены потребители:», «отсутствует по
    адресам:»); берём всё после последнего такого зачина и оставляем только
    похожие на адреса абзацы. Так отсекается хвост вроде «Ориентировочное
    время восстановления электроснабжения — 15:00», который идёт следом.
    """
    body = _FOOTER_RE.sub('', post_text)
    last = None
    for last in _ADDR_LEADIN_RE.finditer(body):
        pass
    if last is None:
        return None

    tail = body[last.end():]
    paragraphs = [p for p in _split_paragraphs(tail) if _ADDRESS_MARKER_RE.search(p)]
    if not paragraphs:
        return None
    return "\n".join(paragraphs)


def extract_addresses(post_text: str, queue: int = None):
    """
    Дословный блок адресов или None.

    queue=None — пост без очереди (по району/адресам), якорь по зачину списка.
    Иначе якорь по объявлению очереди; None, если очередь объявлена несколько
    раз (привязка адресов неоднозначна — публикуем без них, чтобы не приписать
    людям чужие улицы).
    """
    if not post_text:
        return None

    if queue is None:
        block = _addresses_without_queue(post_text)
        if not block:
            return None
        if len(block) < 20 or not _ADDRESS_MARKER_RE.search(block):
            return None
        return block

    blocks = _segments_by_queue(post_text).get(queue) or []
    if len(blocks) != 1:
        if len(blocks) > 1:
            logger.warning(
                f"Очередь {queue} объявлена в посте {len(blocks)} раза — "
                "привязка адресов неоднозначна, публикуем без адресов"
            )
        return None

    block = blocks[0]
    if len(block) < 20 or not _ADDRESS_MARKER_RE.search(block):
        logger.warning(f"Блок после объявления очереди {queue} не похож на адреса — публикуем без них")
        return None
    return block


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


def _fit(parts: list, addresses: str) -> str:
    """
    Собирает текст, укладываясь в лимит: при нужде подрезает адреса по запятой.

    Подрезаем ИСХОДНЫЙ текст и только потом накладываем разметку — иначе
    обрезка могла бы разорвать HTML-тег и сломать публикацию.
    """
    text = "\n".join(parts + ["", _prepare_addresses(addresses), "", _ADDRESSES_NOTE])
    if len(text) <= MAX_MESSAGE_LEN:
        return text

    tail = "…"
    # Всё, кроме самих адресов: шапка, сноска, разметка, экранирование.
    # После обрезки абзацев станет меньше, значит запас только вырастет.
    overhead = len(text) - len(addresses) + len(tail)
    room = MAX_MESSAGE_LEN - overhead
    if room < 200:
        logger.warning("Не хватает места даже под урезанные адреса — публикуем без них")
        return "\n".join(parts)

    cut = addresses[:room]
    boundary = max(cut.rfind(','), cut.rfind('\n'))
    if boundary > room // 2:
        cut = cut[:boundary]
    cut = cut.rstrip(' ,\n') + tail
    logger.info(f"Адреса подрезаны под лимит сообщения: {len(addresses)} → {len(cut)} символов")
    return "\n".join(parts + ["", _prepare_addresses(cut), "", _ADDRESSES_NOTE])


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
                       time_from, time_to, restore=None, addresses: str = None) -> str:
        parts = [header]

        time_line = self._time_line(time_from, time_to, restore)
        if time_line:
            parts += ["", time_line]

        source = self._source_line()
        if source:
            parts += ["", source]

        if not addresses:
            return "\n".join(parts)

        # Очередь в подзаголовке — только если она в посте реально названа
        suffix = f" ({queue} очередь)" if queue else ""
        parts += ["", f"<b>{addresses_title}{suffix}:</b>"]
        return _fit(parts, addresses)

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
                      restore=None, addresses: str = None, partial: bool = False) -> str:
        """Отключение в рамках ГВО. queue=None — отключение без номера очереди."""
        suffix = self._queue_suffix(queue, partial)
        if confirmed:
            header = f"🔌 <b>Отключение электроэнергии{suffix}</b>"
        else:
            header = f"⚠️ <b>Ожидается отключение электроэнергии{suffix}</b>"
        return self._format_window(
            header, "Адреса отключения", queue, time_from, time_to, restore, addresses
        )

    def format_supply(self, queue, time_from, time_to, restore=None,
                      addresses: str = None, partial: bool = False) -> str:
        """
        Пост «где свет БУДЕТ» — антипод отключения.

        Источник пишет «ориентировочно будет», гарантии он не даёт, поэтому
        и заголовок не обещает твёрдо.
        """
        suffix = self._queue_suffix(queue, partial)
        header = f"💡 <b>Ориентировочно будет свет{suffix}</b>"
        return self._format_window(
            header, "Адреса", queue, time_from, time_to, restore, addresses
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
            if msg_type == 'supply':
                messages.append(
                    self.format_supply(queue, w.get('from'), w.get('to'),
                                       w.get('restore'), addresses, partial)
                )
            else:
                messages.append(
                    self.format_outage(queue, w.get('from'), w.get('to'), confirmed,
                                       w.get('restore'), addresses, partial)
                )
        return messages
