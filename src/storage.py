import asyncio
import json
import os
from typing import Dict, Set
import aiofiles


class PostStorage:
    """
    Хранилище ID обработанных постов, разделённое по каналам.

    ID постов уникальны только в пределах одного канала, поэтому для нескольких
    источников ID нужно ключевать по каналу. Формат файла:
        {"processed_posts": {"@channel_a": [1,2], "@channel_b": [1,2]}}

    Старый формат (плоский список) автоматически мигрируется в default_channel.
    """

    def __init__(self, file_path: str = 'data/processed_posts.json', default_channel: str = 'default'):
        self.file_path = file_path
        self.default_channel = default_channel
        self.processed: Dict[str, Set[int]] = {}
        # Сериализует запись файла: цикл тревог и энергоцикл пишут в один файл
        self._lock = asyncio.Lock()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

    async def load(self):
        self.processed = {}
        if not os.path.exists(self.file_path):
            return
        try:
            async with aiofiles.open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.loads(await f.read())
            stored = data.get('processed_posts', {})
            if isinstance(stored, list):
                # Старый формат — все ID относятся к основному каналу
                self.processed[self.default_channel] = set(stored)
            elif isinstance(stored, dict):
                self.processed = {ch: set(ids) for ch, ids in stored.items()}
        except Exception as e:
            print(f"Ошибка при загрузке данных: {e}")
            self.processed = {}

    async def save(self):
        try:
            data = {
                'processed_posts': {ch: list(ids) for ch, ids in self.processed.items()}
            }
            async with aiofiles.open(self.file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Ошибка при сохранении данных: {e}")

    def is_processed(self, post_id: int, channel: str = None) -> bool:
        channel = channel or self.default_channel
        return post_id in self.processed.get(channel, set())

    async def mark_processed(self, post_id: int, channel: str = None):
        channel = channel or self.default_channel
        async with self._lock:
            self.processed.setdefault(channel, set()).add(post_id)
            await self.save()

    def count(self, channel: str = None) -> int:
        channel = channel or self.default_channel
        return len(self.processed.get(channel, set()))

    @property
    def processed_posts(self) -> Set[int]:
        """Обратная совместимость: ID основного канала."""
        return self.processed.get(self.default_channel, set())
