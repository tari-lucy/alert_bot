"""
Чтение стены ВКонтакте через официальный API (метод wall.get).

Второй источник сигналов оповещения. Появился потому, что в телеграм автор
публикует нестабильно и с опозданием, а в ВК те же сигналы выходят раньше.

Пуш-событий для чужой стены ВК не даёт (Callback API и Bots Long Poll доступны
только администратору своего сообщества), поэтому источник опрашивается, как и
телеграм. Интервал берём такой же — лимитов API это не касается даже близко,
но лишнего внимания к токену не привлекаем.

Посты приводятся к единому виду VKPost, чтобы вызывающий код не знал разницы
между источниками: те же поля id / date / text, что у сообщения телеграма.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import aiohttp

from .logger import setup_logger

logger = setup_logger('vk_parser')

REQUEST_TIMEOUT = 15
PHOTO_TIMEOUT = 20

# Ограничение размера скачиваемой картинки — защита от того, что по ссылке
# окажется что-то неожиданно большое. Плашки весят ~200 КБ.
MAX_PHOTO_BYTES = 8 * 1024 * 1024


@dataclass
class VKPost:
    """Пост стены в том же виде, в каком код работает с сообщениями телеграма."""
    id: int
    date: datetime
    text: str
    photo_urls: List[str] = field(default_factory=list)
    is_pinned: bool = False
    owner_id: int = 0

    @property
    def url(self) -> str:
        return f"https://vk.ru/wall{self.owner_id}_{self.id}"


class VKParser:
    def __init__(self, token: str, domain: str, api_version: str = '5.199',
                 base_url: str = 'https://api.vk.ru/method'):
        """
        Args:
            token: сервисный ключ доступа приложения ВК
            domain: короткое имя страницы (напр. 'razvozhaev_m')
            api_version: версия API
        """
        self.token = token
        self.domain = domain
        self.api_version = api_version
        self.base_url = base_url.rstrip('/')
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def get_latest_posts(self, limit: int = 10) -> List[VKPost]:
        """
        Последние посты стены, новые первыми. При любой ошибке — пустой список.

        Ошибку не поднимаем наружу: цикл опроса должен пережить сбой сети или
        временный отказ API и попробовать снова на следующей итерации.
        """
        try:
            session = await self._get_session()
            async with session.get(
                f'{self.base_url}/wall.get',
                params={
                    'domain': self.domain,
                    'count': limit,
                    'access_token': self.token,
                    'v': self.api_version,
                },
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"ВК API вернул HTTP {resp.status}: {body[:200]}")
                    return []
                payload = await resp.json()
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при чтении стены ВК (>{REQUEST_TIMEOUT}с)")
            return []
        except Exception as e:
            logger.error(f"Ошибка при чтении стены ВК: {e}")
            return []

        # ВК отдаёт ошибки со статусом 200 — разбираем тело.
        if 'error' in payload:
            err = payload['error']
            code = err.get('error_code')
            logger.error(f"Ошибка ВК API {code}: {err.get('error_msg')}")
            return []

        items = (payload.get('response') or {}).get('items') or []
        posts = [self._parse_post(item) for item in items]
        posts = [p for p in posts if p is not None]
        # Закреплённый пост ВК поднимает наверх независимо от даты, поэтому
        # порядок из ответа ненадёжен — сортируем сами.
        posts.sort(key=lambda p: p.date, reverse=True)
        logger.info(f"Получено {len(posts)} постов со стены ВК {self.domain}")
        return posts

    def _parse_post(self, item: dict) -> Optional[VKPost]:
        try:
            photos = []
            for att in item.get('attachments') or []:
                if att.get('type') != 'photo':
                    continue
                sizes = (att.get('photo') or {}).get('sizes') or []
                if not sizes:
                    continue
                # Самый крупный размер: у плашек мелкие превью нечитаемы, а
                # перцептивный хеш к размеру нечувствителен.
                photos.append(max(sizes, key=lambda s: s.get('width', 0))['url'])

            return VKPost(
                id=int(item['id']),
                date=datetime.fromtimestamp(int(item['date']), tz=timezone.utc),
                text=(item.get('text') or '').strip(),
                photo_urls=photos,
                is_pinned=bool(item.get('is_pinned')),
                owner_id=int(item.get('owner_id', 0)),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error(f"Не удалось разобрать пост ВК: {e}")
            return None

    async def download_photo(self, url: str) -> Optional[bytes]:
        """Байты картинки или None. Ошибку не поднимаем — пост просто не распознается."""
        try:
            session = await self._get_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=PHOTO_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Не удалось скачать картинку, HTTP {resp.status}")
                    return None
                # Читаем ПОЛНОСТЬЮ, кусками: одиночный read(n) возвращает лишь
                # то, что успело прийти, и картинка приезжала обрезанной —
                # распознать такую невозможно.
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_PHOTO_BYTES:
                        logger.error(f"Картинка больше {MAX_PHOTO_BYTES} байт — пропускаем")
                        return None
                return bytes(data)
        except asyncio.TimeoutError:
            logger.error(f"Таймаут при скачивании картинки (>{PHOTO_TIMEOUT}с)")
            return None
        except Exception as e:
            logger.error(f"Ошибка при скачивании картинки: {e}")
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
