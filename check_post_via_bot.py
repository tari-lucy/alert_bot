#!/usr/bin/env python3
import asyncio
import sys
from telegram import Bot
from src.config import Config
from src.filter import TextFilter

async def check_post_via_bot(post_id):
    """Проверка поста через Bot API"""
    if not Config.BOT_TOKEN:
        print("BOT_TOKEN не настроен!")
        return

    bot = Bot(token=Config.BOT_TOKEN)

    try:
        # Получаем информацию о канале
        chat_id = Config.SOURCE_CHANNEL

        # Пробуем получить сообщение
        try:
            # Для каналов нужно использовать chat_id и message_id
            message = await bot.forward_message(
                chat_id=Config.BOT_TOKEN.split(':')[0],  # Отправляем себе
                from_chat_id=chat_id,
                message_id=post_id
            )
            await bot.delete_message(
                chat_id=Config.BOT_TOKEN.split(':')[0],
                message_id=message.message_id
            )
        except Exception as e:
            print(f"Не удалось получить пост через Bot API: {e}")
            print("\nПопробуйте вручную проверить текст поста в канале @razvozhaev")
            return

    finally:
        # Закрываем сессию бота
        await bot.session.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Использование: python check_post_via_bot.py <post_id>")
        print("\nИли введите текст поста вручную для проверки:")
        text = input("Введите текст: ").strip()

        if text:
            signals = Config.signals()
            text_filter = TextFilter(
                {name: spec['keywords_file'] for name, spec in signals.items()}
            )
            text_filter.load_keywords()

            normalized = text_filter._normalize_text(text)
            message_type = text_filter.get_message_type(text)

            print(f"\nИсходный текст: {text}")
            print(f"Нормализованный текст: '{normalized}'")
            print(f"Вид сигнала: {message_type}")

            for name, phrases in text_filter.keywords.items():
                print(f"\n=== Фразы сигнала «{signals[name]['title']}» (нормализованные) ===")
                for keyword in sorted(phrases):
                    match = "✅ СОВПАДАЕТ" if normalized.startswith(keyword) else ""
                    print(f"  '{keyword}' {match}")
        sys.exit(0)

    post_id = int(sys.argv[1])
    asyncio.run(check_post_via_bot(post_id))
