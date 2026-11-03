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

    # Сигнал «Морская опасность» и его отбой. Источник даёт этот сигнал при
    # атаке морскими дронами; он не отменяет и не заменяет воздушную тревогу —
    # оба сигнала могут действовать одновременно.
    KEYWORDS_SEA_ALERT_FILE = os.getenv('KEYWORDS_SEA_ALERT_FILE', 'data/keywords_sea_alert.txt')
    KEYWORDS_SEA_CLEAR_FILE = os.getenv('KEYWORDS_SEA_CLEAR_FILE', 'data/keywords_sea_clear.txt')
    SEA_ALERT_TEMPLATE = os.getenv('SEA_ALERT_TEMPLATE', '')
    SEA_CLEAR_TEMPLATE = os.getenv('SEA_CLEAR_TEMPLATE', '')

    # Обратная совместимость (старый KEYWORDS_FILE)
    KEYWORDS_FILE = os.getenv('KEYWORDS_FILE', KEYWORDS_ALERT_FILE)

    # Защита от дублей: не публиковать тот же шаблон повторно в этом окне.
    # Источник иногда постит тревогу/отбой двумя одинаковыми сообщениями
    # подряд (разные ID, разница ~1 сек). Окно много меньше реального
    # интервала между разными тревогами, так что повторную не теряем.
    ALERT_DEDUP_SECONDS = int(os.getenv('ALERT_DEDUP_SECONDS', '180'))

    # Антидубль энергопостов: гасим ТОЛЬКО идентичный текст, вышедший меньше
    # этого окна назад (источник изредка постит одно и то же дважды за секунды).
    # Окно короткое НАМЕРЕННО: адреса/график закономерно повторяются от поста к
    # посту, и одинаковый контент спустя время — это нормальное новое объявление,
    # его глушить нельзя.
    ENERGY_DEDUP_SECONDS = int(os.getenv('ENERGY_DEDUP_SECONDS', '300'))

    # Окно, в котором повторный ТОТ ЖЕ сигнал считается дублем. В отличие от
    # ALERT_DEDUP_SECONDS привязано не к шаблону, а к состоянию сигнала, и
    # сбрасывается противоположным сигналом (отбой обнуляет тревогу и наоборот).
    # Окно широкое, потому что один сигнал приходит из двух источников с
    # разбегом в минуты: в ВК он появляется раньше, чем в телеграме. Но оно
    # НЕ бесконечное — иначе пропущенный отбой заглушил бы следующую тревогу.
    SIGNAL_DEDUP_SECONDS = int(os.getenv('SIGNAL_DEDUP_SECONDS', '1800'))

    # Гибридный режим: использовать Bot API для публикации (опционально)
    BOT_TOKEN = os.getenv('BOT_TOKEN', '')  # Если указан - публикация через Bot API
    USE_BOT_FOR_PUBLISHING = bool(BOT_TOKEN)

    # MAX Messenger (опционально — оставьте пустым для отключения)
    MAX_BOT_TOKEN = os.getenv('MAX_BOT_TOKEN', '')
    MAX_TARGET_CHANNEL = os.getenv('MAX_TARGET_CHANNEL', '')
    MAX_ENABLED = bool(MAX_BOT_TOKEN and MAX_TARGET_CHANNEL)

    # ВКонтакте — второй источник сигналов оповещения. Появился потому, что в
    # телеграм автор публикует нестабильно и с опозданием, а часть сигналов
    # вообще выкладывает картинкой без подписи.
    VK_SERVICE_TOKEN = os.getenv('VK_SERVICE_TOKEN', '')
    VK_DOMAIN = os.getenv('VK_DOMAIN', '')  # короткое имя страницы, напр. razvozhaev_m
    VK_API_VERSION = os.getenv('VK_API_VERSION', '5.199')
    VK_ENABLED = bool(VK_SERVICE_TOKEN and VK_DOMAIN)
    # Интервал опроса ВК. Такой же, как у телеграма: лимиты API позволяют
    # намного чаще, но привлекать внимание к токену незачем.
    VK_CHECK_INTERVAL = int(os.getenv('VK_CHECK_INTERVAL', CHECK_INTERVAL))

    # Распознавание картинок-плашек, которыми публикуются сигналы.
    PLATES_DIR = os.getenv('PLATES_DIR', 'data/plates')
    # Модель для чтения НЕЗНАКОМОЙ картинки. Знакомые плашки узнаются по
    # перцептивному хешу бесплатно и мгновенно, модель включается редко —
    # только когда источник нарисует новую плашку.
    VISION_MODEL = os.getenv('VISION_MODEL', 'openai/gpt-4.1-nano')

    # Второй источник — канал энергетиков (Севастопольэнерго): отключения по очередям.
    # Посты преобразуются через LLM и публикуются в те же цели (TG + MAX).
    ENERGY_SOURCE_CHANNEL = os.getenv('ENERGY_SOURCE_CHANNEL', '')
    # Адреса берутся дословно из самого поста Севастопольэнерго (см. energy_formatter),
    # локальные списки по очередям больше не используются.
    # Подпись источника в публикуемых энергопостах (текстом, без ссылки)
    ENERGY_SOURCE_NAME = os.getenv('ENERGY_SOURCE_NAME', 'Севастопольэнерго')

    # Публиковать посты о СНЯТИИ ограничений (lifted). Включено с 17.07.2026:
    # раньше выключали из-за ложных срабатываний на локальных восстановлениях
    # («в 14:20 восстановлено электроснабжение ул. Ленина»), теперь их отсекает
    # предохранитель ГВО (is_gvo_post) ещё до вызова LLM.
    ENERGY_LIFTED_ENABLED = os.getenv('ENERGY_LIFTED_ENABLED', 'true').lower() == 'true'

    # Публиковать объявления о самом графике ГВО («переходим на 2 через 6»).
    ENERGY_REGIME_ENABLED = os.getenv('ENERGY_REGIME_ENABLED', 'true').lower() == 'true'

    # Публиковать посты «где свет БУДЕТ» (supply) — источник сообщает, каким
    # очередям и когда ориентировочно дадут свет. Включено: людям это полезно.
    ENERGY_SUPPLY_ENABLED = os.getenv('ENERGY_SUPPLY_ENABLED', 'true').lower() == 'true'

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
    # Действующее состояние сигналов (тревога/отбой по группам). Переживает
    # перезапуск, иначе рестарт между приходом сигнала из ВК и из телеграма
    # давал бы дубль.
    SIGNAL_STATE_FILE = 'data/signal_state.json'
    LOG_FILE = 'logs/bot.log'

    @classmethod
    def signals(cls) -> dict:
        """
        Таблица видов сигналов: имя → файл фраз, шаблон публикации, группа.

        «Группа» — это то, что сигнал включает и выключает: у воздушной тревоги
        и её отбоя группа общая («air»), поэтому отбой гасит тревогу. Морская
        опасность живёт в своей группе и на воздушную тревогу не влияет —
        сигналы могут действовать одновременно.

        Новый вид сигнала = файл фраз + шаблон в .env + строка здесь.
        Сигнал без заполненного шаблона просто не подключается: так его можно
        держать выключенным, не удаляя фразы.
        """
        table = {
            'alert': {
                'group': 'air', 'kind': 'alert',
                'keywords_file': cls.KEYWORDS_ALERT_FILE, 'template': cls.ALERT_TEMPLATE,
                'title': 'Воздушная тревога',
            },
            'clear': {
                'group': 'air', 'kind': 'clear',
                'keywords_file': cls.KEYWORDS_CLEAR_FILE, 'template': cls.CLEAR_TEMPLATE,
                'title': 'Отбой воздушной тревоги',
            },
            'sea_alert': {
                'group': 'sea', 'kind': 'alert',
                'keywords_file': cls.KEYWORDS_SEA_ALERT_FILE, 'template': cls.SEA_ALERT_TEMPLATE,
                'title': 'Морская опасность',
            },
            'sea_clear': {
                'group': 'sea', 'kind': 'clear',
                'keywords_file': cls.KEYWORDS_SEA_CLEAR_FILE, 'template': cls.SEA_CLEAR_TEMPLATE,
                'title': 'Отбой морской опасности',
            },
        }
        return {
            name: spec for name, spec in table.items()
            if spec['template'].strip() and os.path.exists(spec['keywords_file'])
        }

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

        # Проверка источника ВК: токен без страницы (и наоборот) бесполезен
        if cls.VK_SERVICE_TOKEN and not cls.VK_DOMAIN:
            raise ValueError("VK_DOMAIN должен быть указан, если задан VK_SERVICE_TOKEN")
        if cls.VK_DOMAIN and not cls.VK_SERVICE_TOKEN:
            raise ValueError("VK_SERVICE_TOKEN должен быть указан, если задан VK_DOMAIN")

        # Сигнал без шаблона молча не публикуется — предупреждаем явно, чтобы
        # заполненные фразы не создавали ложного ощущения работающего сигнала.
        for name, spec in (('sea_alert', cls.SEA_ALERT_TEMPLATE),
                           ('sea_clear', cls.SEA_CLEAR_TEMPLATE)):
            if not spec.strip():
                print(f"ВНИМАНИЕ: шаблон сигнала «{name}» пуст — сигнал публиковаться не будет")

        return True
