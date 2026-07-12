"""
Извлечение структуры из постов канала Севастопольэнерго через LLM (vsellm.ru).

Отправляет текст поста в OpenAI-совместимый шлюз и получает строгий JSON:
    {"type": "outage|lifted|ignore",
     "confirmed": true|false,
     "windows": [{"queue": 1|2, "from": "HH:MM"|null, "to": "HH:MM"}]}

Итоговый текст для публикации собирает НЕ модель, а код (energy_formatter),
поэтому галлюцинации модели не попадают в готовый пост: используются только
провалидированные поля (очередь 1/2, время в формате ЧЧ:ММ).
"""
import json
import re
import aiohttp
from .logger import setup_logger

logger = setup_logger('llm_extractor')

SYSTEM_PROMPT = """Ты извлекаешь структуру из постов Telegram-канала «Севастопольэнерго» об ограничении электроснабжения (веерные отключения по очередям). Верни ТОЛЬКО JSON, без пояснений и markdown.

Схема:
{"type": "outage|lifted|ignore", "confirmed": true|false, "windows": [{"queue": 1|2, "from": "ЧЧ:ММ или null", "to": "ЧЧ:ММ"}]}

Правила:
- "outage": пост сообщает, что электроснабжение ограничивается/ограничено/обесточено по графику конкретной очереди в конкретный интервал времени. Формулировки бывают разные и равнозначны: "будет ограничено", "ограничено", "обесточены потребители по графику N очереди", "введён режим", "отключение по N очереди" и т.п.
  - confirmed=true, если ограничение уже введено/действует/вводится ("введён режим", "обесточены потребители", "будет ограничено по графику N очереди", "с ЧЧ до ЧЧ обесточены").
  - confirmed=false, если это предупреждение/условие ("если режим не будет отменён, то с ЧЧ до ЧЧ будут введены ограничения по графику N очереди").
- "lifted": ТОЛЬКО глобальное снятие всего режима веерных ограничений ("режим временного ограничения электроснабжения снят", "диспетчер дал команду включить всех потребителей"). НЕ lifted, если про будущее ("как только ограничения будут сняты").
- "ignore": всё остальное — новости, телеэфиры, поздравления, реклама канала в МАКС/МАХ, общая информация без конкретного окна и очереди, посты "введён режим, но точного графика пока нет", а ТАКЖЕ восстановление электроснабжения на КОНКРЕТНЫХ улицах/адресах или после аварии/"технологического нарушения" (это локальное восстановление, а не снятие режима веерных отключений).
- windows: если в посте несколько интервалов/очередей — верни несколько объектов. "from" = null, если указано только "До ЧЧ.ММ". Время в 24-часовом формате ЧЧ:ММ. Очередь только 1 или 2.
- Игнорируй в тексте упоминания о ВОССТАНОВЛЕНИИ очереди ("потребителям 2 очереди возвращается свет") — это не окно отключения; бери только объявленные ОТКЛЮЧЕНИЯ.

Примеры:
Пост: "По команде диспетчера введён режим. До 12.00 электроснабжение будет ограничено по графику 2 очереди."
JSON: {"type":"outage","confirmed":true,"windows":[{"queue":2,"from":null,"to":"12:00"}]}

Пост: "По команде диспетчера введён режим временного ограничения. С 9:00 до 12:00 обесточены потребители по графику 2 очереди. Ленинский район: ул. ..."
JSON: {"type":"outage","confirmed":true,"windows":[{"queue":2,"from":"09:00","to":"12:00"}]}

Пост: "Режим продолжает действовать. Если он не будет отменён, то с 15:00 до 18:00 будут введены ограничения по графику 1-й очереди."
JSON: {"type":"outage","confirmed":false,"windows":[{"queue":1,"from":"15:00","to":"18:00"}]}

Пост: "Режим временного ограничения электроснабжения снят. Диспетчер дал команду включить всех потребителей."
JSON: {"type":"lifted","confirmed":true,"windows":[]}

Пост: "В прямом эфире телеканала ответил на вопросы севастопольцев..."
JSON: {"type":"ignore","confirmed":false,"windows":[]}

Пост: "Технологическое нарушение 12 июля. В 10:45 электроснабжение потребителей ул. Рабочая, Розы Люксембург восстановлено."
JSON: {"type":"ignore","confirmed":false,"windows":[]}
"""


def _validate(data: dict):
    """Проверяет структуру ответа LLM. Возвращает нормализованный dict или None."""
    if not isinstance(data, dict):
        return None

    msg_type = data.get('type')
    if msg_type not in ('outage', 'lifted', 'ignore'):
        return None

    if msg_type != 'outage':
        return {'type': msg_type, 'confirmed': bool(data.get('confirmed')), 'windows': []}

    raw_windows = data.get('windows')
    if not isinstance(raw_windows, list) or not raw_windows:
        return None

    time_re = re.compile(r'^([01]?\d|2[0-3]):[0-5]\d$')

    def norm(t: str) -> str:
        h, m = t.split(':')
        return f"{int(h):02d}:{int(m):02d}"

    windows = []
    for w in raw_windows:
        if not isinstance(w, dict):
            return None
        queue = w.get('queue')
        if queue not in (1, 2):
            return None

        t_to = w.get('to')
        if not isinstance(t_to, str) or not time_re.match(t_to):
            return None
        t_to = norm(t_to)

        t_from = w.get('from')
        if t_from in (None, '', 'null'):
            t_from = None
        elif isinstance(t_from, str) and time_re.match(t_from):
            t_from = norm(t_from)
        else:
            return None

        windows.append({'queue': queue, 'from': t_from, 'to': t_to})

    return {'type': 'outage', 'confirmed': bool(data.get('confirmed')), 'windows': windows}


def verify_outage(extraction: dict, post_text: str):
    """
    Сверяет извлечённые очередь и время с текстом исходного поста — защита от
    уверенных ошибок модели (правильный формат, но неверные данные).

    Возвращает (True, '') если извлечённые очередь и все времёна реально
    присутствуют в тексте, иначе (False, 'причина').
    Проверяем только outage (у lifted нет очереди/времени).
    """
    if extraction.get('type') != 'outage':
        return True, ''

    low = post_text.lower()

    # Все часы, упомянутые в тексте (форматы ЧЧ:ММ / ЧЧ.ММ и «с/до/по ЧЧ»)
    hours = {int(h) for h in re.findall(r'(\d{1,2})[:.]\d{2}', low)}
    hours |= {int(h) for h in re.findall(r'(?:с|до|по|к)\s+(\d{1,2})\b', low)}

    words = {1: 'перв', 2: 'втор'}
    for w in extraction.get('windows', []):
        q = w['queue']
        if not re.search(rf'{q}\s*-?\s*[а-я]{{0,2}}\s*очеред', low) and words[q] not in low:
            return False, f"очередь {q} не найдена в тексте поста"
        for t in (w.get('from'), w.get('to')):
            if t is None:
                continue
            if int(t.split(':')[0]) not in hours:
                return False, f"время {t} не найдено в тексте поста"

    return True, ''


def _parse_json(content: str):
    """Достаёт JSON из ответа модели (снимает возможные ```-обёртки)."""
    content = content.strip()
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Иногда модель добавляет текст вокруг — вырезаем первый {...} блок
        m = re.search(r'\{.*\}', content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


class LLMExtractor:
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

    async def extract(self, post_text: str):
        """
        Возвращает провалидированный dict {type, confirmed, windows}
        или None, если извлечь не удалось (ошибка сети/модели/валидации).
        """
        if not post_text or not post_text.strip():
            return None

        try:
            session = await self._get_session()
            async with session.post(
                f'{self.base_url}/chat/completions',
                json={
                    'model': self.model,
                    'temperature': 0,
                    'max_tokens': 400,
                    'messages': [
                        {'role': 'system', 'content': SYSTEM_PROMPT},
                        {'role': 'user', 'content': post_text},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Ошибка LLM API {resp.status}: {body[:300]}")
                    return None
                payload = await resp.json()
        except Exception as e:
            logger.error(f"Ошибка запроса к LLM: {e}")
            return None

        try:
            content = payload['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            logger.error(f"Неожиданный ответ LLM: {str(payload)[:300]}")
            return None

        data = _parse_json(content)
        result = _validate(data)
        if result is None:
            logger.error(f"LLM вернул невалидную структуру. Ответ: {content[:300]}")
        return result

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
