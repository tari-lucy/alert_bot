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
from src.llm_extractor import LLMExtractor, verify_windows, is_gvo_post
from src.energy_formatter import EnergyFormatter
from src.vk_parser import VKParser
from src.plate_recognizer import PlateRecognizer
from src.vision_recognizer import VisionRecognizer
from src.signal_state import SignalState
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

        # Виды сигналов заданы данными (см. Config.signals): фразы + шаблон.
        self.signals = Config.signals()
        self.filter = TextFilter(
            {name: spec['keywords_file'] for name, spec in self.signals.items()}
        )
        # Дедуп по состоянию, а не по времени: один сигнал приходит из двух
        # источников с разбегом в минуты, и старое окно в 180 секунд его уже
        # не ловило.
        self.signal_state = SignalState(
            Config.SIGNAL_STATE_FILE, ttl_seconds=Config.SIGNAL_DEDUP_SECONDS
        )
        self._skip_old = True  # При старте фильтруем старые посты

        # Источник сигналов ВКонтакте (опционально). Нужен потому, что в
        # телеграм автор публикует нестабильно и с опозданием, а часть сигналов
        # выкладывает картинкой без подписи — текстом их не поймать.
        self.vk_parser = None
        self.plate_recognizer = None
        self.vision = None
        self._vk_skip_old = True
        self._vk_attempts = {}  # id поста -> неудачных попыток распознать картинку
        if Config.VK_ENABLED:
            logger.info(f"🔵 ВК: читаем стену {Config.VK_DOMAIN} (интервал {Config.VK_CHECK_INTERVAL}с)")
            self.vk_parser = VKParser(
                token=Config.VK_SERVICE_TOKEN,
                domain=Config.VK_DOMAIN,
                api_version=Config.VK_API_VERSION,
            )
            if Config.LLM_API_KEY and Config.VISION_MODEL:
                self.vision = VisionRecognizer(
                    api_key=Config.LLM_API_KEY,
                    base_url=Config.LLM_BASE_URL,
                    model=Config.VISION_MODEL,
                )
                logger.info(f"👁 Незнакомые картинки читает {Config.VISION_MODEL}")
            self.plate_recognizer = PlateRecognizer(Config.PLATES_DIR, vision=self.vision)

        # Второй источник — канал энергетиков (опционально)
        self.energy_parser = None
        self.llm_extractor = None
        self.energy_formatter = None
        self._energy_skip_old = True
        self._energy_recent = {}  # текст -> время публикации (антидубль в окне)
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

        await self.signal_state.load()
        logger.info(f"Состояние сигналов: {self.signal_state.describe()}")

        # Загрузка ключевых фраз
        try:
            counts = self.filter.load_keywords()
            logger.info(
                "Инициализирован фильтр сигналов: "
                + ", ".join(f"{self.signals[n]['title']} — {c} фраз" for n, c in counts.items())
            )

            if not counts or sum(counts.values()) == 0:
                logger.warning("ВНИМАНИЕ: Списки ключевых фраз пусты! Сообщения не будут публиковаться.")

        except Exception as e:
            logger.error(f"Ошибка при инициализации фильтра: {e}")
            raise

        # Эталоны картинок-плашек: часть сигналов источник публикует картинкой
        # без подписи, текстом их не поймать вовсе.
        if self.plate_recognizer:
            if self.plate_recognizer.load_plates() == 0:
                logger.warning(
                    "ВНИМАНИЕ: эталонов плашек нет — картинки будут распознаваться "
                    "только зрением (медленнее) или не распознаваться вовсе"
                )

    async def stop(self):
        await self.client.disconnect()
        if self.max_publisher:
            await self.max_publisher.close()
        if self.llm_extractor:
            await self.llm_extractor.close()
        if self.vk_parser:
            await self.vk_parser.close()
        if self.vision:
            await self.vision.close()
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

            # Определяем вид сигнала (воздушная тревога, отбой, морская опасность…)
            signal = self.filter.check_message(message)

            if signal:
                status = await self.publish_signal(signal, message.id, source='TG')
                if status == 'failed':
                    # Не помечаем обработанным — повторим на следующей итерации
                    continue
                await self.storage.mark_processed(message.id)
                if status == 'published':
                    new_posts_count += 1
            else:
                # Пост не совпал с ключевыми фразами
                logger.debug(
                    f"Пост {message.id} не совпадает с ключевыми фразами. "
                    f"Текст: '{message.text[:100]}...'"
                )
                # Похоже на сигнал, но не опознано — источник мог сменить
                # формулировку. Раньше такой пост исчезал молча.
                await self._warn_unmatched(message.text, f"телеграм, пост {message.id}")
                await self.storage.mark_processed(message.id)
                filtered_posts_count += 1

        # Итоговая статистика
        if new_posts_count > 0:
            logger.info(f"Опубликовано сообщений: {new_posts_count}")

        if filtered_posts_count > 0:
            logger.debug(f"Отфильтровано постов (не совпали): {filtered_posts_count}")

    async def publish_signal(self, name: str, source_id, source: str = '') -> str:
        """
        Публикует шаблон сигнала. Возвращает 'published' | 'duplicate' | 'failed'.

        Дубли гасятся по СОСТОЯНИЮ группы сигналов, а не по времени публикации
        шаблона. Так ловится и повтор источника (два одинаковых поста подряд),
        и та же тревога, пришедшая из второго источника минутами позже: в ВК
        сигналы выходят раньше, чем в телеграме. Настоящая повторная тревога
        после отбоя при этом проходит — состояние группы к тому моменту уже
        переключено отбоем.
        """
        spec = self.signals.get(name)
        if not spec:
            logger.error(f"Неизвестный вид сигнала «{name}» — публикация отменена")
            return 'failed'

        title = spec['title']
        logger.info(f"Сигнал «{title}» из источника {source or '?'} (пост {source_id})")

        if self.signal_state.is_duplicate(spec['group'], spec['kind']):
            logger.warning(
                f"«{title}» уже действует (состояние: {self.signal_state.describe()}) — "
                f"повтор не публикуем"
            )
            return 'duplicate'

        success = await self.publish_to_all(spec['template'], source_message_id=source_id)
        if not success:
            logger.error(f"Не удалось опубликовать сигнал «{title}» (пост {source_id})")
            return 'failed'

        await self.signal_state.remember(spec['group'], spec['kind'])
        logger.info(f"Сигнал «{title}» опубликован (пост {source_id}, источник {source or '?'})")
        return 'published'

    async def _warn_unmatched(self, text: str, where: str):
        """
        Сообщает администратору о тексте, который похож на сигнал, но не опознан.

        Страховка от тихого отказа: источник меняет формулировки (так появилась
        «Морская опасность», которой не было в списках), и раньше несовпавший
        пост просто исчезал в логах. Публиковать такое нельзя — только показать
        человеку, чтобы он добавил фразу в файл.
        """
        if not self.filter.looks_like_signal(text):
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.warning(f"Похоже на сигнал, но не опознано ({where}): {text[:120]!r}")
        await self.notify_admin(
            f"[AlertBot] {now}\n"
            f"Похоже на сигнал, но не совпало ни с одной фразой ({where}).\n"
            f"Проверьте и при необходимости добавьте фразу в data/keywords_*.txt:\n\n"
            f"{text[:600]}"
        )

    async def _signal_from_vk_post(self, post):
        """
        Вид сигнала для поста ВК и способ распознавания, либо (None, причина).

        Порядок важен для цены и скорости:
        1) текст поста — бесплатно и мгновенно;
        2) если текста НЕТ, а картинка есть — сверка с эталонами по хешу, тоже
           бесплатно;
        3) и только если картинка незнакомая — зрение LLM.

        Посты, где текст ЕСТЬ, но это не сигнал, — обычные новости с
        иллюстрациями; в их картинки не смотрим вовсе. Плашки сигналов
        приходят без подписи, так что ничего не теряем, зато не тратим запросы
        к модели на каждое фото со стены.
        """
        signal = self.filter.get_message_type(post.text)
        if signal:
            return signal, 'текст'
        if post.text:
            await self._warn_unmatched(post.text, f"ВК, пост {post.id}")
            return None, 'обычный пост'
        if not post.photo_urls or not self.plate_recognizer:
            return None, 'нет картинок'

        data = await self.vk_parser.download_photo(post.photo_urls[0])
        if not data:
            return None, 'картинку не скачать'

        text, how = await self.plate_recognizer.recognize(data)
        if not text:
            return None, 'картинка не распознана'

        signal = self.filter.get_message_type(text)
        if not signal:
            # Что-то прочли, но это не сигнал: либо картинка не плашка, либо
            # источник придумал новую формулировку — второе важно показать.
            await self._warn_unmatched(text, f"ВК, картинка в посте {post.id}")
            return None, 'распознано, но не сигнал'

        if how == 'vision':
            # Новая плашка: сохраняем в эталоны, чтобы следующий раз она
            # узнавалась мгновенно и без обращения к модели.
            saved = self.plate_recognizer.remember_plate(data, text, prefix=signal)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await self.notify_admin(
                f"[AlertBot] {now}\n"
                f"Новая картинка-плашка распознана зрением как «{text}» "
                f"и добавлена в эталоны ({saved or 'файл не сохранён'}).\n"
                f"Пост: {post.url}"
            )

        return signal, f'картинка/{how}'

    async def process_vk_posts(self):
        channel = f'vk:{Config.VK_DOMAIN}'
        posts = await self.vk_parser.get_latest_posts(limit=10)

        # После старта — старые посты помечаем без обработки, свежие оставляем
        if self._vk_skip_old:
            fresh = 0
            for post in posts:
                if self.storage.is_processed(post.id, channel):
                    continue
                if self._is_message_fresh(post):
                    fresh += 1
                else:
                    await self.storage.mark_processed(post.id, channel)
            self._vk_skip_old = False
            if fresh == 0:
                return
            logger.info(f"ВК: {fresh} свежих постов будут обработаны")

        for post in reversed(posts):
            if self.storage.is_processed(post.id, channel):
                continue

            signal, how = await self._signal_from_vk_post(post)

            if signal:
                self._vk_attempts.pop(post.id, None)
                status = await self.publish_signal(signal, post.id, source=f'ВК/{how}')
                if status == 'failed':
                    # Ошибка публикации — не помечаем, повторим позже
                    continue
                await self.storage.mark_processed(post.id, channel)
                continue

            if how in ('картинку не скачать', 'картинка не распознана'):
                # Временная неудача (сеть, шлюз) — пробуем ещё, но не вечно:
                # молчаливо потерять сигнал нельзя, но и крутить его бесконечно
                # тоже незачем.
                attempts = self._vk_attempts.get(post.id, 0) + 1
                self._vk_attempts[post.id] = attempts
                if attempts < 3:
                    logger.warning(f"ВК пост {post.id}: {how}, попытка {attempts} — повторим")
                    continue
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                await self.notify_admin(
                    f"[AlertBot] {now}\n"
                    f"Пост ВК без текста с картинкой не удалось распознать "
                    f"за {attempts} попытки ({how}). Проверьте вручную:\n{post.url}"
                )
                self._vk_attempts.pop(post.id, None)

            await self.storage.mark_processed(post.id, channel)

    async def _vk_loop(self):
        """
        Отдельный фоновый цикл для стены ВК.

        Изолирован от цикла телеграма: скачивание картинки и обращение к зрению
        занимают секунды, и задерживать из-за них проверку основного источника
        нельзя.
        """
        logger.info(f"ВК: запущен отдельный цикл (интервал {Config.VK_CHECK_INTERVAL}с)")
        while True:
            try:
                await self.process_vk_posts()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка в цикле ВК: {e}", exc_info=True)
            await asyncio.sleep(Config.VK_CHECK_INTERVAL)

    def _is_duplicate_content(self, text: str) -> bool:
        """
        True, если ТОЧНО такой же текст публиковался меньше окна дедупа назад.

        Окно короткое: ловим настоящие дубли (источник постит одно и то же
        дважды за секунды), но НЕ глушим закономерно повторяющийся контент
        (адреса/график повторяются от поста к посту — это нормальные новые
        объявления).
        """
        last = self._energy_recent.get(text)
        if last is None:
            return False
        return datetime.now() - last < timedelta(seconds=Config.ENERGY_DEDUP_SECONDS)

    async def _publish_energy(self, extraction: dict, message_id: int, post_text: str = ''):
        """
        Собирает и публикует посты из результата извлечения.

        Возвращает (published, failed): сколько сообщений реально опубликовано и
        сколько НЕ удалось из-за ошибки. Пропуск дубля — это не ошибка.
        """
        published = 0
        failed = 0
        # Подчистим протухшие записи окна, чтобы словарь не рос бесконечно.
        cutoff = datetime.now() - timedelta(seconds=Config.ENERGY_DEDUP_SECONDS)
        self._energy_recent = {t: ts for t, ts in self._energy_recent.items() if ts >= cutoff}

        for text in self.energy_formatter.build_messages(extraction, post_text):
            if self._is_duplicate_content(text):
                logger.info(f"Энергопост {message_id}: дубль контента в окне {Config.ENERGY_DEDUP_SECONDS}с, пропуск")
                continue
            success = await self.publish_to_all(text, source_message_id=message_id)
            if success:
                self._energy_recent[text] = datetime.now()
                published += 1
            else:
                failed += 1
        return published, failed

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
            # Репосты не наши: канал иногда пересылает Развожаева, а он тоже
            # пишет про отключения — такой пост прошёл бы фильтр по словам.
            if message.fwd_from:
                logger.debug(f"Энергопост {message.id}: репост — пропускаем")
                await self.storage.mark_processed(message.id, channel)
                continue

            # raw_text — текст без разметки. У message.text парс-мод Telethon
            # подставляет markdown-маркеры entity исходного поста (__курсив__,
            # **жирный**), и они попадали бы в публикацию обычными символами.
            post_text = message.raw_text
            if not post_text:
                # Текст может появиться позже — не помечаем обработанным
                continue

            # Предохранитель: публикуем только посты про график временных
            # ограничений. Стоит ДО LLM — модель не может его обойти, а на
            # ремонты/аварии/вакансии запрос к ней даже не уходит.
            if not is_gvo_post(post_text):
                logger.debug(f"Энергопост {message.id}: не про ГВО — пропускаем")
                await self.storage.mark_processed(message.id, channel)
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

            if extraction['type'] == 'supply' and not Config.ENERGY_SUPPLY_ENABLED:
                logger.info(f"Энергопост {message.id}: 'подача света' отключена настройкой — не публикуем")
                await self.storage.mark_processed(message.id, channel)
                continue

            if extraction['type'] == 'regime' and not Config.ENERGY_REGIME_ENABLED:
                logger.info(f"Энергопост {message.id}: 'объявление режима' отключено настройкой — не публикуем")
                await self.storage.mark_processed(message.id, channel)
                continue

            # Сверка извлечённых данных с текстом поста (защита от ошибок модели)
            ok, reason = verify_windows(extraction, post_text)
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

            published, failed = await self._publish_energy(extraction, message.id, post_text)
            if failed > 0:
                # Реальная ошибка публикации (сеть/API) — НЕ помечаем, повторим позже.
                logger.error(f"Энергопост {message.id}: ошибка публикации ({failed}) — повторим позже (тип {extraction['type']})")
            elif published > 0:
                await self.storage.mark_processed(message.id, channel)
                logger.info(f"Энергопост {message.id}: опубликовано сообщений: {published} (тип {extraction['type']})")
            else:
                # Всё содержимое — дубль в окне; это не ошибка, пост уже отработан.
                await self.storage.mark_processed(message.id, channel)
                logger.info(f"Энергопост {message.id}: контент уже публиковался в окне — пропуск (тип {extraction['type']})")

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
        vk_task = None

        try:
            logger.info(f"Начат мониторинг канала {Config.SOURCE_CHANNEL}")
            logger.info(f"Целевой канал TG: {Config.TARGET_CHANNEL}")
            if Config.MAX_ENABLED:
                logger.info(f"Целевой канал MAX: {Config.MAX_TARGET_CHANNEL}")
            if Config.VK_ENABLED:
                logger.info(f"Источник сигналов ВК: vk.ru/{Config.VK_DOMAIN}")
            if Config.ENERGY_ENABLED:
                logger.info(f"Второй источник (энергоканал): {Config.ENERGY_SOURCE_CHANNEL}, модель LLM: {Config.LLM_MODEL}")
            logger.info(f"Интервал проверки: {Config.CHECK_INTERVAL} секунд")
            logger.info(f"Виды сигналов: {', '.join(s['title'] for s in self.signals.values())}")

            if Config.RANDOM_DELAY:
                logger.info(f"Случайные задержки включены: ±{Config.DELAY_VARIANCE} секунд")

            # Энергоканал крутится в отдельной задаче, чтобы не влиять на тревоги
            if self.energy_parser:
                energy_task = asyncio.create_task(self._energy_loop())

            # ВК — тоже отдельной задачей: он не зависит от соединения с
            # телеграмом и не должен ждать его переподключения.
            if self.vk_parser:
                vk_task = asyncio.create_task(self._vk_loop())

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
            for task in (energy_task, vk_task):
                if task:
                    task.cancel()
                    try:
                        await task
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
