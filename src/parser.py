import asyncio
from telethon import TelegramClient
from telethon.tl.types import Message
from typing import List, Optional
from .logger import setup_logger

logger = setup_logger('parser')

REQUEST_TIMEOUT = 30


class TelegramParser:
    def __init__(self, client: TelegramClient, source_channel: str):
        self.client = client
        self.source_channel = source_channel

    async def get_latest_posts(self, limit: int = 10) -> List[Message]:
        try:
            messages = await asyncio.wait_for(
                self._fetch_messages(limit), timeout=REQUEST_TIMEOUT
            )
            logger.info(f"Получено {len(messages)} постов из канала {self.source_channel}")
            return messages
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при получении постов (>{REQUEST_TIMEOUT}с)")
            return []
        except Exception as e:
            logger.error(f"Ошибка при получении постов: {e}")
            return []

    async def _fetch_messages(self, limit: int) -> List[Message]:
        messages = []
        async for message in self.client.iter_messages(self.source_channel, limit=limit):
            if message.text or message.media:
                messages.append(message)
        return messages

    async def get_post_by_id(self, post_id: int) -> Optional[Message]:
        try:
            message = await asyncio.wait_for(
                self.client.get_messages(self.source_channel, ids=post_id),
                timeout=REQUEST_TIMEOUT
            )
            return message
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при получении поста {post_id}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении поста {post_id}: {e}")
            return None
