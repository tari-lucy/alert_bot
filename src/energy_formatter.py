"""
Сборка готового текста поста из провалидированных данных извлечения
(см. llm_extractor) + фиксированных списков адресов по очередям.

Текст собирается кодом, а не LLM — гарантируется корректность формата,
времени и адресов. Один пост = один текст (без картинок), публикуется
как в TG, так и в MAX через общий publish_alert_template.
"""
import os
from .logger import setup_logger

logger = setup_logger('energy_formatter')

LIFTED_TEXT = (
    "✅ <b>Электроснабжение восстанавливается</b>\n\n"
    "Режим временного ограничения электроснабжения снят.\n"
    "Пожалуйста, не включайте все электроприборы одновременно — "
    "подключайте технику постепенно."
)


class EnergyFormatter:
    def __init__(self, queue_address_files: dict, source_name: str = ''):
        """
        Args:
            queue_address_files: {1: 'путь/queue_1.txt', 2: 'путь/queue_2.txt'}
            source_name: подпись источника (напр. "Севастопольэнерго")
        """
        self.queue_address_files = queue_address_files
        self.source_name = source_name
        self._addr_cache: dict = {}

    def _source_line(self) -> str:
        if self.source_name:
            return f'<i>Об этом сообщило «{self.source_name}».</i>'
        return ''

    def _addresses(self, queue: int) -> str:
        if queue in self._addr_cache:
            return self._addr_cache[queue]
        path = self.queue_address_files.get(queue)
        text = ''
        if path and os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = [ln for ln in f.read().splitlines() if not ln.lstrip().startswith('#')]
                text = "\n".join(lines).strip()
            except Exception as e:
                logger.error(f"Не удалось прочитать адреса очереди {queue}: {e}")
        else:
            logger.warning(f"Файл адресов очереди {queue} не найден: {path}")
        self._addr_cache[queue] = text
        return text

    def format_outage(self, queue: int, time_from, time_to: str, confirmed: bool) -> str:
        if confirmed:
            header = f"🔌 <b>Отключение электроэнергии — {queue} очередь</b>"
        else:
            header = f"⚠️ <b>Ожидается отключение электроэнергии — {queue} очередь</b>"

        time_line = f"🕐 {time_from}–{time_to}" if time_from else f"🕐 До {time_to}"
        parts = [header, "", time_line]

        source = self._source_line()
        if source:
            parts += ["", source]

        addresses = self._addresses(queue)
        if addresses:
            parts += ["", f"<b>Адреса отключения ({queue} очередь):</b>", "", addresses]

        return "\n".join(parts)

    def format_lifted(self) -> str:
        source = self._source_line()
        return LIFTED_TEXT + (f"\n\n{source}" if source else '')

    def build_messages(self, extraction: dict) -> list:
        """Список готовых текстов для публикации. Для outage с несколькими окнами — несколько."""
        msg_type = extraction.get('type')
        if msg_type == 'lifted':
            return [self.format_lifted()]
        if msg_type == 'outage':
            confirmed = extraction.get('confirmed', False)
            return [
                self.format_outage(w['queue'], w['from'], w['to'], confirmed)
                for w in extraction.get('windows', [])
            ]
        return []
