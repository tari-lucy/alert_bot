import json
import os
from typing import Set
import aiofiles

class PostStorage:
    def __init__(self, file_path: str = 'data/processed_posts.json'):
        self.file_path = file_path
        self.processed_posts: Set[int] = set()
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

    async def load(self):
        if os.path.exists(self.file_path):
            try:
                async with aiofiles.open(self.file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    data = json.loads(content)
                    self.processed_posts = set(data.get('processed_posts', []))
            except Exception as e:
                print(f"Ошибка при загрузке данных: {e}")
                self.processed_posts = set()
        else:
            self.processed_posts = set()

    async def save(self):
        try:
            data = {'processed_posts': list(self.processed_posts)}
            async with aiofiles.open(self.file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Ошибка при сохранении данных: {e}")

    def is_processed(self, post_id: int) -> bool:
        return post_id in self.processed_posts

    async def mark_processed(self, post_id: int):
        self.processed_posts.add(post_id)
        await self.save()
