#!/usr/bin/env python3
"""
Скрипт для получения ID канала
Запустите: python3 get_channel_id.py
"""
import asyncio
from telethon import TelegramClient
from src.config import Config

async def get_channel_info():
    client = TelegramClient(
        Config.SESSION_NAME,
        Config.API_ID,
        Config.API_HASH
    )

    await client.start(phone=Config.PHONE)

    print("\n=== ИНФОРМАЦИЯ О КАНАЛАХ ===\n")

    # Исходный канал
    try:
        source = await client.get_entity(Config.SOURCE_CHANNEL)
        print(f"Исходный канал:")
        print(f"  Username: @{source.username if source.username else 'нет'}")
        print(f"  ID: {source.id}")
        print(f"  Title: {source.title}")
        print()
    except Exception as e:
        print(f"Ошибка получения исходного канала: {e}\n")

    # Целевой канал
    try:
        target = await client.get_entity(Config.TARGET_CHANNEL)
        print(f"Целевой канал:")
        print(f"  Username: @{target.username if target.username else 'нет'}")
        print(f"  ID: {target.id}")
        print(f"  Title: {target.title}")

        # Проверка прав
        if hasattr(target, 'creator') and target.creator:
            print(f"  ✅ Вы создатель канала")
        elif hasattr(target, 'admin_rights') and target.admin_rights:
            print(f"  ✅ Вы администратор")
            if target.admin_rights.post_messages:
                print(f"  ✅ Есть права на публикацию")
            else:
                print(f"  ❌ НЕТ прав на публикацию!")
        else:
            print(f"  ❌ Вы НЕ администратор канала!")
        print()
    except Exception as e:
        print(f"Ошибка получения целевого канала: {e}\n")

    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(get_channel_info())
