from telethon import TelegramClient
from telethon.tl.types import Message
from typing import List, Optional
from .logger import setup_logger

logger = setup_logger('parser')

class TelegramParser:
    def __init__(self, client: TelegramClient, source_channel: str):
        self.client = client
        self.source_channel = source_channel

    async def get_latest_posts(self, limit: int = 10) -> List[Message]:
        try:
            messages = []
            async for message in self.client.iter_messages(self.source_channel, limit=limit):
                if message.text or message.media:
                    messages.append(message)

            logger.info(f"Получено {len(messages)} постов из канала {self.source_channel}")
            return messages
        except Exception as e:
            logger.error(f"Ошибка при получении постов: {e}")
            return []

    async def get_post_by_id(self, post_id: int) -> Optional[Message]:
        try:
            message = await self.client.get_messages(self.source_channel, ids=post_id)
            return message
        except Exception as e:
            logger.error(f"Ошибка при получении поста {post_id}: {e}")
            return None
