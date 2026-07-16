#!/usr/bin/env python3
import asyncio
import random
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError
from telegram import Bot
from src.config import Config
from src.parser import TelegramParser
from src.publisher import TelegramPublisher
from src.bot_publisher import BotPublisher
from src.max_publisher import MaxPublisher
from src.storage import PostStorage
from src.filter import TextFilter
from src.llm_extractor import LLMExtractor, verify_outage
from src.energy_formatter import EnergyFormatter
from src.logger import setup_logger

logger = setup_logger('main')

# Посты старше этого порога считаются устаревшими после перезапуска/переподключения
STALE_THRESHOLD = timedelta(minutes=5)


class AlertBot:
    def __init__(self):
        Config.validate()

        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH
        )

        self.parser = TelegramParser(self.client, Config.SOURCE_CHANNEL)

        # Список паблишеров (публикуем во все параллельно)
        self.publishers = []

        # Telegram publisher
        if Config.USE_BOT_FOR_PUBLISHING:
            logger.info("🤖 TG: Userbot читает → Bot API публикует")
            self.publishers.append(BotPublisher(Config.BOT_TOKEN, Config.TARGET_CHANNEL))
        else:
            logger.info("👤 TG: Userbot читает и публикует")
            self.publishers.append(TelegramPublisher(self.client, Config.TARGET_CHANNEL))

        # MAX publisher (опционально)
        self.max_publisher = None
        if Config.MAX_ENABLED:
            logger.info(f"📱 MAX: Публикация в канал {Config.MAX_TARGET_CHANNEL}")
            self.max_publisher = MaxPublisher(Config.MAX_BOT_TOKEN, Config.MAX_TARGET_CHANNEL)
            self.publishers.append(self.max_publisher)

        self.storage = PostStorage(Config.DATA_FILE, default_channel=Config.SOURCE_CHANNEL)
        self.filter = TextFilter(
            alert_keywords_file=Config.KEYWORDS_ALERT_FILE,
            clear_keywords_file=Config.KEYWORDS_CLEAR_FILE
        )
        self._skip_old = True  # При старте фильтруем старые посты

        # Второй источник — канал энергетиков (опционально)
        self.energy_parser = None
        self.llm_extractor = None
        self.energy_formatter = None
        self._energy_skip_old = True
        self._energy_recent = []  # Контент-дедуп дублей (одинаковый текст с разницей в секунды)
        self._energy_attempts = {}  # message_id -> число неудачных попыток извлечения
        if Config.ENERGY_ENABLED:
            logger.info(f"⚡ Энергоканал: читаем {Config.ENERGY_SOURCE_CHANNEL} → LLM → TG+MAX")
            self.energy_parser = TelegramParser(self.client, Config.ENERGY_SOURCE_CHANNEL)
            self.llm_extractor = LLMExtractor(
                api_key=Config.LLM_API_KEY,
                base_url=Config.LLM_BASE_URL,
                model=Config.LLM_MODEL,
            )
            self.energy_formatter = EnergyFormatter(
                source_name=Config.ENERGY_SOURCE_NAME,
            )

    async def publish_to_all(self, template_text: str, source_message_id: int = None) -> bool:
        """Публикует во все настроенные каналы параллельно."""
        results = await asyncio.gather(*[
            pub.publish_alert_template(template_text, source_message_id)
            for pub in self.publishers
        ], return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Паблишер {i} вызвал исключение: {result}")
            elif not result:
                logger.error(f"Паблишер {i} не смог опубликовать")

        success_count = sum(1 for r in results if r is True)
        return success_count > 0

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
        if self.max_publisher:
            await self.max_publisher.close()
        if self.llm_extractor:
            await self.llm_extractor.close()
        logger.info("Бот остановлен")

    def _is_message_fresh(self, message) -> bool:
        """Проверяет, свежее ли сообщение (моложе STALE_THRESHOLD)."""
        if not message.date:
            return False
        now = datetime.now(timezone.utc)
        message_age = now - message.date
        return message_age < STALE_THRESHOLD

    async def process_new_posts(self):
        messages = await self.parser.get_latest_posts(limit=10)

        # После старта/переподключения — старые посты пропускаем, свежие обрабатываем
        if self._skip_old:
            skipped = 0
            fresh = 0
            for message in messages:
                if self.storage.is_processed(message.id):
                    continue
                if self._is_message_fresh(message):
                    fresh += 1
                    # Свежий пост — не помечаем, оставляем для нормальной обработки
                else:
                    await self.storage.mark_processed(message.id)
                    skipped += 1
            if skipped > 0:
                logger.info(f"Пропущено {skipped} устаревших постов (старше {STALE_THRESHOLD})")
            if fresh > 0:
                logger.info(f"Найдено {fresh} свежих необработанных постов — будут обработаны")
            self._skip_old = False
            if fresh == 0:
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
                success = await self.publish_to_all(
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
                success = await self.publish_to_all(
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

    async def _publish_energy(self, extraction: dict, message_id: int, post_text: str = '') -> int:
        """Собирает и публикует посты из результата извлечения. Возвращает число опубликованных."""
        published = 0
        for text in self.energy_formatter.build_messages(extraction, post_text):
            if text in self._energy_recent:
                logger.info(f"Энергопост {message_id}: дубль контента, пропуск")
                continue
            success = await self.publish_to_all(text, source_message_id=message_id)
            if success:
                self._energy_recent.append(text)
                if len(self._energy_recent) > 30:
                    self._energy_recent.pop(0)
                published += 1
        return published

    async def process_energy_posts(self):
        channel = Config.ENERGY_SOURCE_CHANNEL
        messages = await self.energy_parser.get_latest_posts(limit=10)

        # После старта/переподключения — старые посты помечаем без обработки, свежие оставляем
        if self._energy_skip_old:
            fresh = 0
            for message in messages:
                if self.storage.is_processed(message.id, channel):
                    continue
                if self._is_message_fresh(message):
                    fresh += 1
                else:
                    await self.storage.mark_processed(message.id, channel)
            self._energy_skip_old = False
            if fresh == 0:
                return
            logger.info(f"Энергоканал: {fresh} свежих постов будут обработаны")

        for message in reversed(messages):
            if self.storage.is_processed(message.id, channel):
                continue
            # raw_text — текст без разметки. У message.text парс-мод Telethon
            # подставляет markdown-маркеры entity исходного поста (__курсив__,
            # **жирный**), и они попадали бы в публикацию обычными символами.
            post_text = message.raw_text
            if not post_text:
                # Текст может появиться позже — не помечаем обработанным
                continue

            extraction = await self.llm_extractor.extract(post_text)

            if extraction is None:
                # Ошибка сети/модели/валидации — ретраим ограниченное число раз
                attempts = self._energy_attempts.get(message.id, 0) + 1
                self._energy_attempts[message.id] = attempts
                if attempts >= 3:
                    logger.error(f"Энергопост {message.id}: извлечение не удалось за {attempts} попытки, пропускаем")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    await self.notify_admin(
                        f"[AlertBot] {now}\n"
                        f"Не удалось разобрать энергопост {message.id} за {attempts} попытки. "
                        f"Проверьте вручную:\n{post_text[:400]}"
                    )
                    await self.storage.mark_processed(message.id, channel)
                    self._energy_attempts.pop(message.id, None)
                else:
                    logger.warning(f"Энергопост {message.id}: попытка {attempts}, повторим позже")
                continue

            self._energy_attempts.pop(message.id, None)

            if extraction['type'] == 'ignore':
                logger.debug(f"Энергопост {message.id}: не релевантен (ignore)")
                await self.storage.mark_processed(message.id, channel)
                continue

            if extraction['type'] == 'lifted' and not Config.ENERGY_LIFTED_ENABLED:
                logger.info(f"Энергопост {message.id}: 'снятие ограничений' отключено настройкой — не публикуем")
                await self.storage.mark_processed(message.id, channel)
                continue

            # Сверка извлечённых данных с текстом поста (защита от ошибок модели)
            ok, reason = verify_outage(extraction, post_text)
            if not ok:
                logger.warning(f"Энергопост {message.id}: сверка не прошла ({reason}) — на ручную модерацию")
                draft = "\n\n— — —\n\n".join(
                    self.energy_formatter.build_messages(extraction, post_text)
                )
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await self.notify_admin(
                    f"[AlertBot] {now}\n"
                    f"Энергопост {message.id}: сверка не прошла ({reason}). "
                    f"Не опубликовано, проверьте вручную.\n\n"
                    f"ИСХОДНЫЙ ПОСТ:\n{post_text[:600]}\n\n"
                    f"ЧЕРНОВИК БОТА:\n{draft[:800]}"
                )
                await self.storage.mark_processed(message.id, channel)
                continue

            published = await self._publish_energy(extraction, message.id, post_text)
            if published > 0:
                await self.storage.mark_processed(message.id, channel)
                logger.info(f"Энергопост {message.id}: опубликовано сообщений: {published} (тип {extraction['type']})")
            else:
                logger.error(f"Энергопост {message.id}: не удалось опубликовать (тип {extraction['type']})")

    async def _energy_loop(self):
        """
        Отдельный фоновый цикл для энергоканала.

        Полностью изолирован от цикла тревог: медленный/зависший запрос к LLM
        не задерживает проверку воздушных тревог. Переподключением к Telegram
        управляет основной цикл; здесь мы просто пропускаем итерацию, если
        клиент временно не подключён.
        """
        logger.info(f"Энергоканал: запущен отдельный цикл (интервал {Config.CHECK_INTERVAL}с)")
        while True:
            try:
                if self.client.is_connected():
                    await self.process_energy_posts()
                else:
                    logger.debug("Энергоканал: клиент не подключён, пропускаем итерацию")
            except FloodWaitError as e:
                logger.warning(f"Энергоканал FloodWaitError: ждём {e.seconds}с")
                await asyncio.sleep(e.seconds)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка в энергоцикле: {e}", exc_info=True)
            await asyncio.sleep(Config.CHECK_INTERVAL)

    async def run(self):
        await self.start()
        energy_task = None

        try:
            logger.info(f"Начат мониторинг канала {Config.SOURCE_CHANNEL}")
            logger.info(f"Целевой канал TG: {Config.TARGET_CHANNEL}")
            if Config.MAX_ENABLED:
                logger.info(f"Целевой канал MAX: {Config.MAX_TARGET_CHANNEL}")
            if Config.ENERGY_ENABLED:
                logger.info(f"Второй источник (энергоканал): {Config.ENERGY_SOURCE_CHANNEL}, модель LLM: {Config.LLM_MODEL}")
            logger.info(f"Интервал проверки: {Config.CHECK_INTERVAL} секунд")

            if Config.RANDOM_DELAY:
                logger.info(f"Случайные задержки включены: ±{Config.DELAY_VARIANCE} секунд")

            # Энергоканал крутится в отдельной задаче, чтобы не влиять на тревоги
            if self.energy_parser:
                energy_task = asyncio.create_task(self._energy_loop())

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
                        self._energy_skip_old = True
                        await self.notify_admin(
                            f"[AlertBot] {now}\n"
                            f"Переподключение успешно. Бот снова работает.\n"
                            f"Свежие посты (до 5 мин) будут опубликованы."
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
            if energy_task:
                energy_task.cancel()
                try:
                    await energy_task
                except asyncio.CancelledError:
                    pass
            await self.stop()

async def main():
    bot = AlertBot()
    await bot.run()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Работа бота завершена")
