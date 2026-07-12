import re
from typing import Set
from telethon.tl.types import Message
from .logger import setup_logger

logger = setup_logger('filter')


class TextFilter:
    """
    Фильтр для проверки текста сообщений на соответствие ключевым фразам.
    Поддерживает точное совпадение с игнорированием эмодзи и регистра.
    Различает типы сообщений: тревога (alert) и отбой (clear).
    """

    def __init__(self, alert_keywords_file: str, clear_keywords_file: str):
        """
        Args:
            alert_keywords_file: Путь к файлу с фразами тревоги
            clear_keywords_file: Путь к файлу с фразами отбоя
        """
        self.alert_keywords_file = alert_keywords_file
        self.clear_keywords_file = clear_keywords_file
        self.alert_keywords: Set[str] = set()
        self.clear_keywords: Set[str] = set()

    def load_keywords(self) -> tuple:
        """
        Загружает ключевые фразы из обоих файлов.

        Returns:
            Кортеж (количество_фраз_тревоги, количество_фраз_отбоя)
        """
        alert_count = self._load_keywords_from_file(
            self.alert_keywords_file,
            self.alert_keywords,
            "тревоги"
        )
        clear_count = self._load_keywords_from_file(
            self.clear_keywords_file,
            self.clear_keywords,
            "отбоя"
        )

        logger.info(
            f"Загружено {alert_count} фраз тревоги и {clear_count} фраз отбоя"
        )
        return (alert_count, clear_count)

    def _load_keywords_from_file(
        self,
        file_path: str,
        keywords_set: Set[str],
        category: str
    ) -> int:
        """
        Загружает ключевые фразы из одного файла.

        Args:
            file_path: Путь к файлу
            keywords_set: Множество для сохранения фраз
            category: Название категории (для логов)

        Returns:
            Количество загруженных фраз
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            keywords_set.clear()

            for line in lines:
                keyword = line.strip()

                # Пропускаем пустые строки и комментарии
                if not keyword or keyword.startswith('#'):
                    continue

                # Нормализуем ключевую фразу при загрузке
                normalized = self._normalize_text(keyword)
                if normalized:
                    keywords_set.add(normalized)

            logger.info(f"Загружено {len(keywords_set)} фраз {category} из {file_path}")
            return len(keywords_set)

        except FileNotFoundError:
            logger.error(f"Файл фраз {category} не найден: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Ошибка при загрузке фраз {category}: {e}")
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
        - Удаляет восклицательные знаки
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

        # Удаляем восклицательные знаки
        text = text.replace('!', '')

        # Нормализация пробелов
        # Заменяем все виды пробельных символов на обычный пробел
        text = re.sub(r'\s+', ' ', text)

        # Убираем пробелы в начале и конце
        text = text.strip()

        # Приведение к нижнему регистру
        text = text.lower()

        return text

    def get_message_type(self, text: str) -> str:
        """
        Определяет тип сообщения по тексту.

        Args:
            text: Текст для проверки

        Returns:
            'alert' - тревога, 'clear' - отбой, None - не совпало
        """
        if not text:
            return None

        normalized_text = self._normalize_text(text)

        # Проверяем тревогу (фраза в начале текста)
        for keyword in self.alert_keywords:
            if normalized_text.startswith(keyword):
                logger.info(f"Найдена ТРЕВОГА. Текст: '{text[:100]}...'")
                return 'alert'

        # Проверяем отбой (фраза в начале текста)
        for keyword in self.clear_keywords:
            if normalized_text.startswith(keyword):
                logger.info(f"Найден ОТБОЙ. Текст: '{text[:100]}...'")
                return 'clear'

        return None

    def check_message(self, message: Message) -> str:
        """
        Проверяет сообщение Telegram и определяет его тип.

        Args:
            message: Объект сообщения Telethon

        Returns:
            'alert' - тревога, 'clear' - отбой, None - не совпало
        """
        if not message or not message.text:
            return None

        return self.get_message_type(message.text)

    def get_keywords_count(self) -> tuple:
        """
        Возвращает количество загруженных ключевых фраз.

        Returns:
            Кортеж (количество_тревог, количество_отбоев)
        """
        return (len(self.alert_keywords), len(self.clear_keywords))
