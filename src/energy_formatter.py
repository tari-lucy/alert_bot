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
import re
from .logger import setup_logger

logger = setup_logger('energy_formatter')

LIFTED_TEXT = (
    "✅ <b>Электроснабжение восстанавливается</b>\n\n"
    "Режим временного ограничения электроснабжения снят.\n"
    "Пожалуйста, не включайте все электроприборы одновременно — "
    "подключайте технику постепенно."
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


def extract_addresses(post_text: str, queue: int):
    """
    Дословный блок адресов для указанной очереди или None.

    None, если блок не найден, не похож на адреса или очередь упомянута
    в посте несколько раз (тогда привязка адресов неоднозначна — публикуем
    без них, чтобы не приписать людям чужие улицы).
    """
    if not post_text:
        return None

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


def _fit(parts: list, addresses: str) -> str:
    """Собирает текст, укладываясь в лимит: при нужде подрезает адреса по запятой."""
    text = "\n".join(parts + ["", addresses, "", _ADDRESSES_NOTE])
    if len(text) <= MAX_MESSAGE_LEN:
        return text

    tail = "…"
    overhead = len(text) - len(addresses) + len(tail)
    room = MAX_MESSAGE_LEN - overhead
    if room < 200:
        logger.warning("Не хватает места даже под урезанные адреса — публикуем без них")
        return "\n".join(parts)

    cut = addresses[:room]
    boundary = max(cut.rfind(','), cut.rfind('\n'))
    if boundary > room // 2:
        cut = cut[:boundary]
    logger.info(f"Адреса подрезаны под лимит сообщения: {len(addresses)} → {len(cut)} символов")
    return "\n".join(parts + ["", cut.rstrip(' ,\n') + tail, "", _ADDRESSES_NOTE])


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

    def format_outage(self, queue: int, time_from, time_to: str, confirmed: bool,
                      addresses: str = None) -> str:
        if confirmed:
            header = f"🔌 <b>Отключение электроэнергии — {queue} очередь</b>"
        else:
            header = f"⚠️ <b>Ожидается отключение электроэнергии — {queue} очередь</b>"

        time_line = f"🕐 {time_from}–{time_to}" if time_from else f"🕐 До {time_to}"
        parts = [header, "", time_line]

        source = self._source_line()
        if source:
            parts += ["", source]

        if not addresses:
            return "\n".join(parts)

        parts += ["", f"<b>Адреса отключения ({queue} очередь):</b>"]
        return _fit(parts, addresses)

    def format_lifted(self) -> str:
        source = self._source_line()
        return LIFTED_TEXT + (f"\n\n{source}" if source else '')

    def build_messages(self, extraction: dict, post_text: str = '') -> list:
        """
        Список готовых текстов для публикации. Для outage с несколькими окнами — несколько.

        post_text — исходный пост канала: из него дословно берутся адреса.
        """
        msg_type = extraction.get('type')
        if msg_type == 'lifted':
            return [self.format_lifted()]
        if msg_type == 'outage':
            confirmed = extraction.get('confirmed', False)
            messages = []
            for w in extraction.get('windows', []):
                addresses = extract_addresses(post_text, w['queue'])
                if not addresses:
                    logger.warning(f"Адреса для очереди {w['queue']} не извлечены — пост без адресов")
                messages.append(
                    self.format_outage(w['queue'], w['from'], w['to'], confirmed, addresses)
                )
            return messages
        return []
