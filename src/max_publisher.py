"""
Публикация через MAX Messenger Bot API.
Используется для параллельной публикации алертов в канал MAX.
"""
import aiohttp
from .logger import setup_logger

logger = setup_logger('max_publisher')


class MaxPublisher:
    BASE_URL = 'https://platform-api.max.ru'

    def __init__(self, bot_token: str, target_channel: str):
        self.bot_token = bot_token
        self.target_channel = target_channel
        self._session: aiohttp.ClientSession = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={'Authorization': self.bot_token}
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
