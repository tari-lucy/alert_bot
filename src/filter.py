import re
from typing import Set
from telethon.tl.types import Message
from .logger import setup_logger

logger = setup_logger('filter')


class TextFilter:
    """
    Фильтр для проверки текста сообщений на соответствие ключевым фразам.
    Поддерживает точное совпадение с игнорированием эмодзи и регистра.
    """

    def __init__(self, keywords_file: str):
        """
        Args:
            keywords_file: Путь к файлу с ключевыми фразами
        """
        self.keywords_file = keywords_file
        self.keywords: Set[str] = set()

    def load_keywords(self) -> int:
        """
        Загружает ключевые фразы из файла.

        Returns:
            Количество загруженных ключевых фраз
        """
        try:
            with open(self.keywords_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            self.keywords.clear()

            for line in lines:
                keyword = line.strip()

                # Пропускаем пустые строки и комментарии
                if not keyword or keyword.startswith('#'):
                    continue

                # Нормализуем ключевую фразу при загрузке
                normalized = self._normalize_text(keyword)
                if normalized:
                    self.keywords.add(normalized)

            logger.info(f"Загружено {len(self.keywords)} ключевых фраз из {self.keywords_file}")
            return len(self.keywords)

        except FileNotFoundError:
            logger.error(f"Файл ключевых фраз не найден: {self.keywords_file}")
            raise
        except Exception as e:
            logger.error(f"Ошибка при загрузке ключевых фраз: {e}")
            raise

    def _remove_emojis(self, text: str) -> str:
        """
        Удаляет все эмодзи из текста.

        Args:
            text: Исходный текст

        Returns:
            Текст без эмодзи
        """
        # Регулярное выражение для удаления эмодзи
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # эмодзи лиц
            "\U0001F300-\U0001F5FF"  # символы и пиктограммы
            "\U0001F680-\U0001F6FF"  # транспорт и символы карт
            "\U0001F1E0-\U0001F1FF"  # флаги
            "\U00002702-\U000027B0"  # дополнительные символы
            "\U000024C2-\U0001F251"
            "\U0001F900-\U0001F9FF"  # дополнительные эмодзи
            "\U0001FA00-\U0001FA6F"
            "\U0001FA70-\U0001FAFF"
            "\U00002600-\U000026FF"  # разные символы
            "\U00002700-\U000027BF"
            "]+",
            flags=re.UNICODE
        )
        return emoji_pattern.sub('', text)

    def _normalize_text(self, text: str) -> str:
        """
        Нормализует текст для сравнения:
        - Удаляет эмодзи
        - Приводит к нижнему регистру
        - Нормализует пробелы

        Args:
            text: Исходный текст

        Returns:
            Нормализованный текст
        """
        if not text:
            return ""

        # Удаляем эмодзи
        text = self._remove_emojis(text)

        # Нормализация пробелов
        # Заменяем все виды пробельных символов на обычный пробел
        text = re.sub(r'\s+', ' ', text)

        # Убираем пробелы в начале и конце
        text = text.strip()

        # Приведение к нижнему регистру
        text = text.lower()

        return text

    def matches_keyword(self, text: str) -> bool:
        """
        Проверяет, совпадает ли текст ПОЛНОСТЬЮ с одной из ключевых фраз.

        Args:
            text: Текст для проверки

        Returns:
            True если текст точно совпадает с ключевой фразой
        """
        if not text or not self.keywords:
            return False

        normalized_text = self._normalize_text(text)

        # Точное совпадение всего текста
        matches = normalized_text in self.keywords

        if matches:
            logger.info(f"Найдено совпадение с ключевой фразой. Исходный текст: '{text[:100]}...'")

        return matches

    def check_message(self, message: Message) -> bool:
        """
        Проверяет сообщение Telegram на соответствие ключевым фразам.

        Args:
            message: Объект сообщения Telethon

        Returns:
            True если текст сообщения совпадает с ключевой фразой
        """
        if not message or not message.text:
            return False

        return self.matches_keyword(message.text)

    def get_keywords_count(self) -> int:
        """Возвращает количество загруженных ключевых фраз."""
        return len(self.keywords)
