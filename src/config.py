import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.getenv('API_ID', 0))
    API_HASH = os.getenv('API_HASH', '')
    PHONE = os.getenv('PHONE', '')

    SOURCE_CHANNEL = os.getenv('SOURCE_CHANNEL', '')
    TARGET_CHANNEL = os.getenv('TARGET_CHANNEL', '')

    CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 300))

    # Случайные задержки для имитации человеческого поведения
    RANDOM_DELAY = os.getenv('RANDOM_DELAY', 'true').lower() == 'true'
    DELAY_VARIANCE = int(os.getenv('DELAY_VARIANCE', 5))  # ±5 секунд по умолчанию

    # Фильтрация и публикация - ТРЕВОГА
    KEYWORDS_ALERT_FILE = os.getenv('KEYWORDS_ALERT_FILE', 'data/keywords_alert.txt')
    ALERT_TEMPLATE = os.getenv('ALERT_TEMPLATE', '')

    # Фильтрация и публикация - ОТБОЙ
    KEYWORDS_CLEAR_FILE = os.getenv('KEYWORDS_CLEAR_FILE', 'data/keywords_clear.txt')
    CLEAR_TEMPLATE = os.getenv('CLEAR_TEMPLATE', '')

    # Обратная совместимость (старый KEYWORDS_FILE)
    KEYWORDS_FILE = os.getenv('KEYWORDS_FILE', KEYWORDS_ALERT_FILE)

    # Гибридный режим: использовать Bot API для публикации (опционально)
    BOT_TOKEN = os.getenv('BOT_TOKEN', '')  # Если указан - публикация через Bot API
    USE_BOT_FOR_PUBLISHING = bool(BOT_TOKEN)

    SESSION_NAME = 'alert_bot_session'
    DATA_FILE = 'data/processed_posts.json'
    LOG_FILE = 'logs/bot.log'

    @classmethod
    def validate(cls):
        if not cls.API_ID or not cls.API_HASH:
            raise ValueError("API_ID и API_HASH должны быть указаны в .env файле")
        if not cls.PHONE:
            raise ValueError("PHONE должен быть указан в .env файле")
        if not cls.SOURCE_CHANNEL:
            raise ValueError("SOURCE_CHANNEL должен быть указан в .env файле")
        if not cls.TARGET_CHANNEL:
            raise ValueError("TARGET_CHANNEL должен быть указан в .env файле")

        # Проверка файлов ключевых фраз
        if not os.path.exists(cls.KEYWORDS_ALERT_FILE):
            raise ValueError(f"Файл ключевых фраз тревоги не найден: {cls.KEYWORDS_ALERT_FILE}")

        if not os.path.exists(cls.KEYWORDS_CLEAR_FILE):
            raise ValueError(f"Файл ключевых фраз отбоя не найден: {cls.KEYWORDS_CLEAR_FILE}")

        # Проверка шаблонов
        if not cls.ALERT_TEMPLATE or cls.ALERT_TEMPLATE.strip() == '':
            raise ValueError("ALERT_TEMPLATE должен быть указан в .env файле")

        if not cls.CLEAR_TEMPLATE or cls.CLEAR_TEMPLATE.strip() == '':
            raise ValueError("CLEAR_TEMPLATE должен быть указан в .env файле")

        return True
