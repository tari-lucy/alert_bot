#!/usr/bin/env python3
import asyncio
from telethon import TelegramClient
from src.config import Config
from src.filter import TextFilter

async def check_post(post_id):
    client = TelegramClient(
        'check_post_session',  # Используем отдельную сессию
        Config.API_ID,
        Config.API_HASH
    )

    await client.start(phone=Config.PHONE)

    # Получаем пост
    message = await client.get_messages(Config.SOURCE_CHANNEL, ids=post_id)

    if message and message.text:
        print(f"\n=== Пост {post_id} ===")
        print(f"Текст: {message.text}")
        print(f"\nДлина текста: {len(message.text)} символов")

        # Проверяем фильтр
        signals = Config.signals()
        text_filter = TextFilter(
            {name: spec['keywords_file'] for name, spec in signals.items()}
        )
        text_filter.load_keywords()

        # Показываем нормализованный текст
        normalized = text_filter._normalize_text(message.text)
        print(f"\nНормализованный текст: '{normalized}'")
        print(f"Длина нормализованного: {len(normalized)} символов")

        # Проверяем тип
        message_type = text_filter.get_message_type(message.text)
        print(f"\nТип сообщения: {message_type}")

        # Показываем ключевые фразы по каждому виду сигнала
        for name, phrases in text_filter.keywords.items():
            print(f"\n=== Фразы сигнала «{signals[name]['title']}» ===")
            for i, keyword in enumerate(sorted(phrases), 1):
                match = "  ← СОВПАДАЕТ" if normalized.startswith(keyword) else ""
                print(f"{i}. '{keyword}' (длина: {len(keyword)}){match}")

    else:
        print(f"Пост {post_id} не найден или не содержит текста")

    await client.disconnect()

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Использование: python check_post.py <post_id>")
        sys.exit(1)

    post_id = int(sys.argv[1])
    asyncio.run(check_post(post_id))
