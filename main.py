#!/usr/bin/env python3
import asyncio
import random
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from src.config import Config
from src.parser import TelegramParser
from src.publisher import TelegramPublisher
from src.bot_publisher import BotPublisher
from src.storage import PostStorage
from src.filter import TextFilter
from src.logger import setup_logger

logger = setup_logger('main')

class AlertBot:
    def __init__(self):
        Config.validate()

        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH
        )

        self.parser = TelegramParser(self.client, Config.SOURCE_CHANNEL)

        # ГИБРИДНЫЙ РЕЖИМ: выбираем publisher
        if Config.USE_BOT_FOR_PUBLISHING:
            logger.info("🤖 Режим: Userbot читает → Bot API публикует (гибридный)")
            self.publisher = BotPublisher(Config.BOT_TOKEN, Config.TARGET_CHANNEL)
        else:
            logger.info("👤 Режим: Userbot читает и публикует")
            self.publisher = TelegramPublisher(self.client, Config.TARGET_CHANNEL)

        self.storage = PostStorage(Config.DATA_FILE)
        self.filter = TextFilter(keywords_file=Config.KEYWORDS_FILE)

    async def start(self):
        await self.client.start(phone=Config.PHONE)
        logger.info("Бот запущен и авторизован")

        await self.storage.load()
        logger.info(f"Загружено {len(self.storage.processed_posts)} обработанных постов")

        # Загрузка ключевых фраз
        try:
            keywords_count = self.filter.load_keywords()
            logger.info(f"Инициализирован фильтр с {keywords_count} ключевыми фразами")

            if keywords_count == 0:
                logger.warning("ВНИМАНИЕ: Список ключевых фраз пуст! Алерты не будут публиковаться.")

            logger.info(f"Шаблон алерта: '{Config.ALERT_TEMPLATE[:50]}...'")

        except Exception as e:
            logger.error(f"Ошибка при инициализации фильтра: {e}")
            raise

    async def stop(self):
        await self.client.disconnect()
        logger.info("Бот остановлен")

    async def process_new_posts(self):
        messages = await self.parser.get_latest_posts(limit=10)

        new_posts_count = 0
        filtered_posts_count = 0

        for message in reversed(messages):
            # Пропускаем уже обработанные посты
            if self.storage.is_processed(message.id):
                continue

            logger.info(f"Проверка нового поста {message.id}")

            # Проверяем наличие текста в посте
            if not message.text:
                logger.debug(f"Пост {message.id} не содержит текста, пропускаем")
                await self.storage.mark_processed(message.id)
                continue

            # Проверка на совпадение с ключевой фразой
            if self.filter.check_message(message):
                logger.info(
                    f"Пост {message.id} совпадает с ключевой фразой! "
                    f"Текст: '{message.text[:100]}...'"
                )

                # Публикуем СТАТИЧНЫЙ ШАБЛОН (не исходный текст!)
                success = await self.publisher.publish_alert_template(
                    template_text=Config.ALERT_TEMPLATE,
                    source_message_id=message.id
                )

                if success:
                    await self.storage.mark_processed(message.id)
                    new_posts_count += 1
                    logger.info(f"Алерт успешно опубликован для поста {message.id}")
                else:
                    logger.error(f"Не удалось опубликовать алерт для поста {message.id}")

            else:
                # Пост не совпал с ключевыми фразами
                logger.debug(
                    f"Пост {message.id} не совпадает с ключевыми фразами. "
                    f"Текст: '{message.text[:100]}...'"
                )
                # Помечаем как обработанный
                await self.storage.mark_processed(message.id)
                filtered_posts_count += 1

        # Итоговая статистика
        if new_posts_count > 0:
            logger.info(f"Опубликовано алертов: {new_posts_count}")

        if filtered_posts_count > 0:
            logger.debug(f"Отфильтровано постов (не совпали): {filtered_posts_count}")

    async def run(self):
        await self.start()

        try:
            logger.info(f"Начат мониторинг канала {Config.SOURCE_CHANNEL}")
            logger.info(f"Целевой канал для алертов: {Config.TARGET_CHANNEL}")
            logger.info(f"Интервал проверки: {Config.CHECK_INTERVAL} секунд")

            if Config.RANDOM_DELAY:
                logger.info(f"Случайные задержки включены: ±{Config.DELAY_VARIANCE} секунд")

            while True:
                try:
                    await self.process_new_posts()
                except FloodWaitError as e:
                    # Telegram просит подождать - это нормально
                    wait_time = e.seconds
                    logger.warning(f"FloodWaitError: ждем {wait_time} секунд по требованию Telegram")
                    await asyncio.sleep(wait_time)
                    continue
                except Exception as e:
                    logger.error(f"Ошибка при обработке постов: {e}", exc_info=True)

                # Случайная задержка для имитации человеческого поведения
                if Config.RANDOM_DELAY:
                    variance = random.randint(-Config.DELAY_VARIANCE, Config.DELAY_VARIANCE)
                    delay = max(10, Config.CHECK_INTERVAL + variance)  # Минимум 10 секунд
                    logger.debug(f"Следующая проверка через {delay} секунд")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(Config.CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Получен сигнал остановки")
        finally:
            await self.stop()

async def main():
    bot = AlertBot()
    await bot.run()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа бота завершена")
