#!/usr/bin/env python3
import asyncio
import random
from datetime import datetime
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telegram import Bot
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
        self.filter = TextFilter(
            alert_keywords_file=Config.KEYWORDS_ALERT_FILE,
            clear_keywords_file=Config.KEYWORDS_CLEAR_FILE
        )
        self._skip_old = False

    async def notify_admin(self, text: str):
        """Отправляет уведомление админу в личку через Bot API."""
        if not Config.ADMIN_CHAT_ID or not Config.BOT_TOKEN:
            return
        try:
            bot = Bot(token=Config.BOT_TOKEN)
            await bot.send_message(chat_id=Config.ADMIN_CHAT_ID, text=text)
            logger.info("Уведомление отправлено админу")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")

    async def start(self):
        await self.client.start(phone=Config.PHONE)
        logger.info("Бот запущен и авторизован")

        await self.storage.load()
        logger.info(f"Загружено {len(self.storage.processed_posts)} обработанных постов")

        # Загрузка ключевых фраз
        try:
            alert_count, clear_count = self.filter.load_keywords()
            logger.info(
                f"Инициализирован фильтр: {alert_count} фраз тревоги, "
                f"{clear_count} фраз отбоя"
            )

            if alert_count == 0 and clear_count == 0:
                logger.warning("ВНИМАНИЕ: Списки ключевых фраз пусты! Сообщения не будут публиковаться.")

            logger.info(f"Шаблон тревоги: '{Config.ALERT_TEMPLATE[:50]}...'")
            logger.info(f"Шаблон отбоя: '{Config.CLEAR_TEMPLATE[:50]}...'")

        except Exception as e:
            logger.error(f"Ошибка при инициализации фильтра: {e}")
            raise

    async def stop(self):
        await self.client.disconnect()
        logger.info("Бот остановлен")

    async def process_new_posts(self):
        messages = await self.parser.get_latest_posts(limit=10)

        # После переподключения — пропускаем накопившиеся посты, не публикуя
        if self._skip_old:
            skipped = 0
            for message in messages:
                if not self.storage.is_processed(message.id):
                    await self.storage.mark_processed(message.id)
                    skipped += 1
            if skipped > 0:
                logger.info(f"Пропущено {skipped} старых постов после переподключения")
            self._skip_old = False
            return

        new_posts_count = 0
        filtered_posts_count = 0

        for message in reversed(messages):
            # Пропускаем уже обработанные посты
            if self.storage.is_processed(message.id):
                continue

            logger.info(f"Проверка нового поста {message.id}")

            # Проверяем наличие текста в посте
            if not message.text:
                logger.debug(f"Пост {message.id} не содержит текста, пропускаем (будет перепроверен)")
                # НЕ помечаем как обработанный - возможно текст будет добавлен позже
                continue

            # Определяем тип сообщения (тревога/отбой)
            message_type = self.filter.check_message(message)

            if message_type == 'alert':
                logger.info(
                    f"Пост {message.id} - ТРЕВОГА! "
                    f"Текст: '{message.text[:100]}...'"
                )

                # Публикуем шаблон ТРЕВОГИ
                success = await self.publisher.publish_alert_template(
                    template_text=Config.ALERT_TEMPLATE,
                    source_message_id=message.id
                )

                if success:
                    await self.storage.mark_processed(message.id)
                    new_posts_count += 1
                    logger.info(f"Тревога успешно опубликована для поста {message.id}")
                else:
                    logger.error(f"Не удалось опубликовать тревогу для поста {message.id}")

            elif message_type == 'clear':
                logger.info(
                    f"Пост {message.id} - ОТБОЙ! "
                    f"Текст: '{message.text[:100]}...'"
                )

                # Публикуем шаблон ОТБОЯ
                success = await self.publisher.publish_alert_template(
                    template_text=Config.CLEAR_TEMPLATE,
                    source_message_id=message.id
                )

                if success:
                    await self.storage.mark_processed(message.id)
                    new_posts_count += 1
                    logger.info(f"Отбой успешно опубликован для поста {message.id}")
                else:
                    logger.error(f"Не удалось опубликовать отбой для поста {message.id}")

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
            logger.info(f"Опубликовано сообщений: {new_posts_count}")

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
                    if not self.client.is_connected():
                        logger.warning("Клиент отключен от Telegram, переподключаемся...")
                        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        await self.notify_admin(
                            f"[AlertBot] {now}\n"
                            f"Потеряно соединение с Telegram. Пытаюсь переподключиться..."
                        )
                        await self.client.connect()
                        if not await self.client.is_user_authorized():
                            logger.error("Сессия не авторизована после переподключения")
                            await self.client.start(phone=Config.PHONE)
                        logger.info("Переподключение успешно")
                        self._skip_old = True
                        await self.notify_admin(
                            f"[AlertBot] {now}\n"
                            f"Переподключение успешно. Бот снова работает.\n"
                            f"Старые посты будут пропущены."
                        )

                    await self.process_new_posts()
                except FloodWaitError as e:
                    # Telegram просит подождать - это нормально
                    wait_time = e.seconds
                    logger.warning(f"FloodWaitError: ждем {wait_time} секунд по требованию Telegram")
                    await asyncio.sleep(wait_time)
                    continue
                except ConnectionError as e:
                    logger.error(f"Ошибка соединения: {e}")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await self.notify_admin(
                        f"[AlertBot] {now}\n"
                        f"Ошибка соединения: {e}\n"
                        f"Повторная попытка через 30 секунд..."
                    )
                    await asyncio.sleep(30)
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
