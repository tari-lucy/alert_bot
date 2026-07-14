"""
Публикация через MAX Messenger Bot API.
Используется для параллельной публикации алертов в канал MAX.
"""
import ssl
from pathlib import Path

import aiohttp
from .logger import setup_logger

logger = setup_logger('max_publisher')

# Сертификат Минцифры (Russian Trusted Root CA). Новый домен MAX API
# platform-api2.max.ru отдаёт TLS-сертификат, выпущенный национальным УЦ
# Минцифры, которого нет в стандартном trust store. Доверие ограничено
# только этим SSL-контекстом (только запросы к MAX), системный trust не меняем.
_MINCIFRY_ROOT_CA = (
    Path(__file__).resolve().parent.parent / 'certs' / 'russian_trusted_root_ca.pem'
)


class MaxPublisher:
    BASE_URL = 'https://platform-api2.max.ru'

    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self._session: aiohttp.ClientSession = None
        self._ssl_context: ssl.SSLContext = None

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            ctx = ssl.create_default_context()
            if _MINCIFRY_ROOT_CA.exists():
                ctx.load_verify_locations(cafile=str(_MINCIFRY_ROOT_CA))
            else:
                logger.error(
                    f"Сертификат Минцифры не найден: {_MINCIFRY_ROOT_CA}. "
                    "TLS к MAX API может не установиться."
                )
            self._ssl_context = ctx
        return self._ssl_context

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._get_ssl_context())
            self._session = aiohttp.ClientSession(
                headers={'Authorization': self.bot_token},
                connector=connector
            )
        return self._session

    async def publish_alert_template(
        self,
        template_text: str,
        source_message_id: int = None
    ) -> bool:
        try:
            if not template_text or template_text.strip() == '':
                logger.error("Попытка опубликовать пустой шаблон")
                return False

            session = await self._get_session()
            async with session.post(
                f'{self.BASE_URL}/messages',
                params={'chat_id': self.target_channel},
                json={
                    'text': template_text,
                    'format': 'html'
                }
            ) as resp:
                if resp.status == 200:
                    log_msg = "Шаблон алерта опубликован в MAX"
                    if source_message_id:
                        log_msg += f" (исходный пост: {source_message_id})"
                    logger.info(log_msg)
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"Ошибка MAX API {resp.status}: {body}")
                    return False

        except Exception as e:
            logger.error(f"Ошибка при публикации в MAX: {e}")
            return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
