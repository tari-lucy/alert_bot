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

    # MAX Messenger (опционально — оставьте пустым для отключения)
    MAX_BOT_TOKEN = os.getenv('MAX_BOT_TOKEN', '')
    MAX_TARGET_CHANNEL = os.getenv('MAX_TARGET_CHANNEL', '')
    MAX_ENABLED = bool(MAX_BOT_TOKEN and MAX_TARGET_CHANNEL)

    # Второй источник — канал энергетиков (Севастопольэнерго): отключения по очередям.
    # Посты преобразуются через LLM и публикуются в те же цели (TG + MAX).
    ENERGY_SOURCE_CHANNEL = os.getenv('ENERGY_SOURCE_CHANNEL', '')
    QUEUE_1_ADDRESSES_FILE = os.getenv('QUEUE_1_ADDRESSES_FILE', 'data/queue_1_addresses.txt')
    QUEUE_2_ADDRESSES_FILE = os.getenv('QUEUE_2_ADDRESSES_FILE', 'data/queue_2_addresses.txt')
    # Подпись источника в публикуемых энергопостах (текстом, без ссылки)
    ENERGY_SOURCE_NAME = os.getenv('ENERGY_SOURCE_NAME', 'Севастопольэнерго')

    # LLM (vsellm.ru — OpenAI-совместимый шлюз) для извлечения структуры из энергопостов
    LLM_API_KEY = os.getenv('LLM_API_KEY', '')
    LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.vsellm.ru/v1')
    LLM_MODEL = os.getenv('LLM_MODEL', 'openai/gpt-4.1-nano')

    # Второй источник активен, только если задан канал и ключ LLM
    ENERGY_ENABLED = bool(ENERGY_SOURCE_CHANNEL and LLM_API_KEY)

    # Уведомления администратора о сбоях
    ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '')

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

        # Проверка MAX: если токен задан, канал тоже должен быть
        if cls.MAX_BOT_TOKEN and not cls.MAX_TARGET_CHANNEL:
            raise ValueError("MAX_TARGET_CHANNEL должен быть указан, если задан MAX_BOT_TOKEN")

        # Проверка второго источника: если канал задан, нужен ключ LLM
        if cls.ENERGY_SOURCE_CHANNEL and not cls.LLM_API_KEY:
            raise ValueError("LLM_API_KEY должен быть указан, если задан ENERGY_SOURCE_CHANNEL")

        return True
