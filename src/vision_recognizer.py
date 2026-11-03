"""
Чтение надписи с незнакомой картинки через vision-модель (OpenAI-совместимый шлюз).

Запасной путь распознавания плашек: штатно картинку узнаёт перцептивный хеш
(см. plate_recognizer) — мгновенно и бесплатно. Модель включается, только если
источник нарисовал НОВУЮ плашку, которой нет в эталонах. Это редкое событие,
поэтому расход на модель незаметен, а вот пропустить из-за него сигнал нельзя.

Модель только ЧИТАЕТ текст с картинки. Решение, сигнал это или нет, принимает
обычный TextFilter по файлам ключевых фраз — тот же, что и для текстовых
постов. Придумать несуществующий сигнал модель поэтому не может: выдуманный
текст просто не совпадёт ни с одной фразой.
"""
import asyncio
import base64

import aiohttp

from .logger import setup_logger

logger = setup_logger('vision_recognizer')

# Задача — прочитать крупную надпись, рассуждать тут не о чем.
PROMPT = (
    "Верни ТОЛЬКО текст, который написан на картинке, одной строкой, "
    "без пояснений и кавычек. Если на картинке нет надписи — верни пустую строку."
)

REQUEST_TIMEOUT = 45
MAX_TOKENS = 300


class VisionRecognizer:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self._session: aiohttp.ClientSession = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                }
            )
        return self._session

    async def recognize(self, image_bytes: bytes):
        """Текст с картинки или None, если прочитать не удалось."""
        if not image_bytes:
            return None

        b64 = base64.b64encode(image_bytes).decode('ascii')
        payload = {
            'model': self.model,
            'temperature': 0,
            'max_tokens': MAX_TOKENS,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': PROMPT},
                    {'type': 'image_url',
                     'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
                ],
            }],
        }

        try:
            session = await self._get_session()
            async with session.post(
                f'{self.base_url}/chat/completions',
                json=payload,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Ошибка vision API {resp.status}: {body[:300]}")
                    return None
                data = await resp.json()
        except asyncio.TimeoutError:
            logger.error(f"Таймаут vision-запроса (>{REQUEST_TIMEOUT}с)")
            return None
        except Exception as e:
            logger.error(f"Ошибка vision-запроса: {e}")
            return None

        try:
            text = (data['choices'][0]['message']['content'] or '').strip()
        except (KeyError, IndexError, TypeError):
            logger.error(f"Неожиданный ответ vision API: {str(data)[:300]}")
            return None

        return text or None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
