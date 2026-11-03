# Интеграция публикации в MAX Messenger

## Контекст

Бот мониторит TG-канал @razvozhaev и публикует алерты тревоги/отбоя в @nts_novosti. Нужно добавить параллельную публикацию в канал MAX Messenger.

**Ограничение**: пользователь не админ в канале-источнике на MAX, поэтому MAX API не позволит читать оттуда сообщения (требуются права админа). Источником триггера остаётся только Telegram. MAX используется **только для публикации**.

## План

### 1. Добавить зависимость `aiohttp`

**Файл**: `requirements.txt`

Добавить `aiohttp>=3.9.0`. Используем прямые HTTP-вызовы к MAX API вместо библиотеки `maxapi`, т.к. нам нужна только отправка сообщений — `maxapi` избыточна (Dispatcher, polling-фреймворк).

### 2. Создать `src/max_publisher.py`

Новый файл по образцу `src/bot_publisher.py`. Реализует тот же метод `publish_alert_template(template_text, source_message_id) -> bool`.

- `POST https://platform-api.max.ru/messages?chat_id={channel_id}`
- Body: `{"text": "...", "format": "html"}`
- Header: `Authorization: <token>`
- Lazy-инициализация `aiohttp.ClientSession`, метод `close()` для очистки

### 3. Обновить `src/config.py`

Добавить:
```python
MAX_BOT_TOKEN = os.getenv('MAX_BOT_TOKEN', '')
MAX_TARGET_CHANNEL = os.getenv('MAX_TARGET_CHANNEL', '')
MAX_ENABLED = bool(MAX_BOT_TOKEN and MAX_TARGET_CHANNEL)
```

В `validate()`: если `MAX_BOT_TOKEN` задан, но `MAX_TARGET_CHANNEL` пуст — ошибка.

### 4. Рефакторинг `main.py`

**4a.** `self.publisher` → `self.publishers: list`

Собираем список паблишеров в `__init__`:
- Всегда добавляем TG BotPublisher (или TelegramPublisher)
- Если `Config.MAX_ENABLED` — добавляем MaxPublisher

**4b.** Новый метод `publish_to_all(template_text, source_message_id) -> bool`

Вызывает все паблишеры через `asyncio.gather()`. Если один упал — другой всё равно отправит. Возвращает True если хотя бы один успешен.

**4c.** Заменить все вызовы `self.publisher.publish_alert_template(...)` на `self.publish_to_all(...)`

**4d.** В `stop()` добавить закрытие aiohttp-сессии MaxPublisher.

### 5. Обновить `.env` и `.env.example`

Добавить:
```env
# MAX Messenger (опционально — оставьте пустым для отключения)
MAX_BOT_TOKEN=
MAX_TARGET_CHANNEL=
```

## Файлы

| Файл | Действие |
|---|---|
| `requirements.txt` | Добавить `aiohttp` |
| `src/max_publisher.py` | **Создать** — публикация в MAX |
| `src/config.py` | Добавить MAX_BOT_TOKEN, MAX_TARGET_CHANNEL, MAX_ENABLED |
| `main.py` | Рефакторинг: список паблишеров + publish_to_all |
| `.env.example` | Добавить переменные MAX |
| `.env` | Добавить переменные MAX (пользователь заполнит) |

## Что НЕ меняется

- `src/parser.py` — источник остаётся только TG
- `src/filter.py` — логика фильтрации не меняется
- `src/storage.py` — ID-шки остаются TG-only, миграция не нужна
- `src/bot_publisher.py` — существующий TG-паблишер не трогаем

## Проверка

1. Установить `aiohttp`: `pip install aiohttp`
2. Создать бота в MAX через @metabot, получить токен
3. Добавить бота админом в целевой канал MAX
4. Заполнить `MAX_BOT_TOKEN` и `MAX_TARGET_CHANNEL` в `.env`
5. Перезапустить бот, дождаться триггера — алерт должен прийти в оба канала
6. Проверить в логах строки от `max_publisher` об успешной публикации

## Ссылки

- [MAX API документация](https://dev.max.ru/docs-api)
- [POST /messages](https://dev.max.ru/docs-api/methods/POST/messages)
- [maxapi на PyPI](https://pypi.org/project/maxapi/) (не используем, но для справки)
