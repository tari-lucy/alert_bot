import re
from typing import Dict, Optional, Set

from .logger import setup_logger

logger = setup_logger('filter')


class TextFilter:
    """
    Определяет, каким сигналом оповещения является текст.

    Сигналов теперь несколько видов (воздушная тревога и её отбой, морская
    опасность и её отбой), и список будет расти: город вводит новые сигналы,
    а мы узнаём о них постфактум. Поэтому виды заданы данными — таблицей
    «имя сигнала → файл ключевых фраз», — и новый сигнал добавляется файлом
    фраз плюс шаблоном в .env, без единой правки кода.

    Сравнение: фраза должна стоять В НАЧАЛЕ текста; эмодзи, регистр и «!»
    игнорируются. Через этот же фильтр проходит текст, распознанный с
    картинки-плашки, поэтому файлы фраз остаются единственным местом, где
    записано, что вообще считается сигналом.
    """

    def __init__(self, signal_files: Dict[str, str]):
        """
        Args:
            signal_files: {имя сигнала: путь к файлу ключевых фраз}.
                Порядок важен: совпадения проверяются в порядке объявления.
        """
        self.signal_files = dict(signal_files)
        self.keywords: Dict[str, Set[str]] = {name: set() for name in signal_files}

    def load_keywords(self) -> Dict[str, int]:
        """
        Загружает фразы всех сигналов. Возвращает {имя сигнала: сколько фраз}.

        Отсутствие файла — ошибка: молча остаться без половины сигналов
        опаснее, чем не запуститься.
        """
        counts = {}
        for name, path in self.signal_files.items():
            counts[name] = self._load_file(name, path)
        total = sum(counts.values())
        logger.info(
            "Загружено фраз: " + ", ".join(f"{n} — {c}" for n, c in counts.items())
            + f" (всего {total})"
        )
        return counts

    def _load_file(self, name: str, file_path: str) -> int:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.error(f"Файл фраз сигнала «{name}» не найден: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Ошибка при загрузке фраз сигнала «{name}»: {e}")
            raise

        phrases = self.keywords.setdefault(name, set())
        phrases.clear()
        for line in lines:
            keyword = line.strip()
            if not keyword or keyword.startswith('#'):
                continue
            normalized = self._normalize_text(keyword)
            if normalized:
                phrases.add(normalized)

        logger.info(f"Сигнал «{name}»: {len(phrases)} фраз из {file_path}")
        return len(phrases)

    def _remove_emojis(self, text: str) -> str:
        """Удаляет эмодзи (в том числе селекторы вариаций вроде ❗️)."""
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
        Приводит текст к виду для сравнения: без эмодзи и «!», нижний регистр,
        одиночные пробелы.

        Восклицательный знак заменяется ПРОБЕЛОМ, а не пустотой: источник
        иногда пишет «ВНИМАНИЕ ВСЕМ!Воздушная тревога» без пробела после знака,
        и при простом удалении слова склеивались в «всемвоздушная», из-за чего
        сигнал не опознавался.
        """
        if not text:
            return ""

        text = self._remove_emojis(text)
        text = text.replace('!', ' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()

    def get_message_type(self, text: str) -> Optional[str]:
        """Имя сигнала, которым является текст, или None."""
        if not text:
            return None

        normalized_text = self._normalize_text(text)

        for name in self.signal_files:
            for keyword in self.keywords.get(name, ()):
                if normalized_text.startswith(keyword):
                    logger.info(f"Опознан сигнал «{name}». Текст: '{text[:100]}'")
                    return name

        return None

    def check_message(self, message) -> Optional[str]:
        """Имя сигнала для сообщения телеграма или None."""
        if not message or not message.text:
            return None
        return self.get_message_type(message.text)

    def looks_like_signal(self, text: str) -> bool:
        """
        Похож ли текст на сигнал оповещения, даже если ни с чем не совпал.

        Нужен для страховки от тихого промаха: источник меняет формулировки, и
        раньше такой пост просто исчезал в логах. Теперь о подозрительном, но
        не опознанном тексте узнаёт администратор.
        """
        if not text:
            return False
        # Сигнал всегда короткий — строка-другая. Ограничение по длине
        # отсекает обычные новости, где те же слова встречаются в тексте
        # («пожарная опасность», «отбой дан вчера»), иначе уведомления
        # превратились бы в поток и их перестали бы читать.
        if len(text) > 200:
            return False
        return bool(re.search(r'тревог|отбой|опасност', text, re.I))

    def get_keywords_count(self) -> Dict[str, int]:
        return {name: len(phrases) for name, phrases in self.keywords.items()}
