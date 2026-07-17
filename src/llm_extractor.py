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

SYSTEM_PROMPT = """Ты извлекаешь структуру из постов Telegram-канала «Севастопольэнерго» о веерных отключениях по очередям. Верни ТОЛЬКО JSON, без пояснений и markdown.

Схема:
{"type": "outage|supply|lifted|ignore", "confirmed": true|false, "windows": [{"queue": 1|2, "from": "ЧЧ:ММ или null", "to": "ЧЧ:ММ"}]}

ГЛАВНОЕ РАЗЛИЧИЕ (читай внимательно, разница бывает в ОДНОМ слове):
- "электроснабжение будет ОГРАНИЧЕНО у потребителей N очереди" → outage (света НЕ будет).
- "электроснабжение (ориентировочно) БУДЕТ у потребителей N очереди" — без слова "ограничено" → supply (свет БУДЕТ).
Формулировки почти совпадают. Сначала найди слова "ограничено"/"обесточены"/"отключение" (outage) либо их отсутствие при "электроснабжение будет" (supply), и только потом решай.

Правила:
- "outage": веерное ОТКЛЮЧЕНИЕ по ГРАФИКУ ОЧЕРЕДИ (1-й или 2-й) в конкретный интервал времени. Обязательно должна быть явно указана очередь (1 или 2). Формулировки бывают разные и равнозначны: "будет ограничено по графику N очереди", "обесточены потребители по графику N очереди", "введён режим ... по N очереди", "отключение по N очереди" и т.п.
  - ВАЖНО: если в посте НЕТ номера очереди (1 или 2) — это НЕ outage, это ignore. Плановые/неотложные ремонтные работы и технологические нарушения/аварии по конкретным адресам БЕЗ графика очереди — это ignore, даже если указаны время и адреса.
  - confirmed=true, если ограничение уже введено/действует/вводится ("введён режим", "обесточены потребители", "будет ограничено по графику N очереди", "с ЧЧ до ЧЧ обесточены").
  - confirmed=false, если это предупреждение/условие ("если режим не будет отменён, то с ЧЧ до ЧЧ будут введены ограничения по графику N очереди").
- "supply": АНТИПОД outage — электроснабжение БУДЕТ (свет дадут/подадут/включат) у потребителей конкретной очереди в указанный интервал. Формулировки: "в ближайшие два часа с ЧЧ:ММ до ЧЧ:ММ электроснабжение ориентировочно будет у части потребителей N очереди", "электроснабжение будет подано потребителям N очереди", "включим/подадим свет потребителям N очереди".
  - Требования те же, что у outage: явно указана очередь (1 или 2) и интервал. Нет очереди → ignore.
  - Фразы-обманки в конце таких постов ("после восстановления электроснабжения приборы включаются одновременно...", "выполняются переключения в сетях", "увеличился дефицит мощности") — это пояснения, они НЕ делают пост ни outage, ни lifted.
- "lifted": ТОЛЬКО глобальное снятие ВСЕГО режима веерных ограничений ("режим временного ограничения электроснабжения снят", "диспетчер дал команду включить всех потребителей"). Если свет дают лишь одной очереди на интервал — это supply, а НЕ lifted. НЕ lifted, если про будущее ("как только ограничения будут сняты").
- "ignore": всё, что не outage/supply/lifted. Сюда входят: новости, телеэфиры, поздравления, реклама канала в МАКС/МАХ, общая информация без графика очереди, посты "введён режим, но точного графика пока нет"; плановые и неотложные РЕМОНТНЫЕ работы по конкретным адресам ("для выполнения неотложных/плановых работ будет ограничено электроснабжение по адресам..."); технологические нарушения и аварии; локальные отключения/восстановления по конкретным улицам без указания очереди.
- windows: если в посте несколько интервалов/очередей — верни несколько объектов. "from" = null, если указано только "До ЧЧ.ММ". Время в 24-часовом формате ЧЧ:ММ. Очередь только 1 или 2.
- Игнорируй в тексте упоминания о ВОССТАНОВЛЕНИИ очереди ("потребителям 2 очереди возвращается свет") — это не окно отключения; бери только объявленные ОТКЛЮЧЕНИЯ.

Примеры:
Пост: "По команде диспетчера введён режим. До 12.00 электроснабжение будет ограничено по графику 2 очереди."
JSON: {"type":"outage","confirmed":true,"windows":[{"queue":2,"from":null,"to":"12:00"}]}

КОНТРАСТНАЯ ПАРА — почти одинаковый текст, противоположный смысл:
Пост: "❗️ До 12.00 электроснабжение будет ограничено у части потребителей 2 очереди по адресам: ул. Хрусталева, ул. Курганная..."
JSON: {"type":"outage","confirmed":true,"windows":[{"queue":2,"from":null,"to":"12:00"}]}

Пост: "Увеличился дефицит мощности, специалисты выполняют переключения в сетях. В ближайшие два часа с 12:00 до 14:00 электроснабжение ориентировочно будет у части потребителей 2 очереди по адресам: Центр города: ул. Гоголя... После восстановления электроснабжения многие приборы включаются одновременно."
JSON: {"type":"supply","confirmed":true,"windows":[{"queue":2,"from":"12:00","to":"14:00"}]}

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

Пост: "Также для выполнения неотложных работ 1 июня временно будет ограничена подача электроэнергии по следующим адресам: с 11:00 до 17:00 г. Балаклава, ул. Кизиловая..."
JSON: {"type":"ignore","confirmed":false,"windows":[]}
"""


def _validate(data: dict):
    """Проверяет структуру ответа LLM. Возвращает нормализованный dict или None."""
    if not isinstance(data, dict):
        return None

    msg_type = data.get('type')
    if msg_type not in ('outage', 'supply', 'lifted', 'ignore'):
        return None

    # Окна времени есть только у outage и supply; у lifted/ignore их нет.
    if msg_type not in ('outage', 'supply'):
        return {'type': msg_type, 'confirmed': bool(data.get('confirmed')), 'windows': []}

    raw_windows = data.get('windows')
    if not isinstance(raw_windows, list) or not raw_windows:
        return None

    # Разрешаем также 24:00 — в объявлениях «до 24:00» означает полночь
    time_re = re.compile(r'^(([01]?\d|2[0-3]):[0-5]\d|24:00)$')

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

    return {'type': msg_type, 'confirmed': bool(data.get('confirmed')), 'windows': windows}


def verify_windows(extraction: dict, post_text: str):
    """
    Сверяет извлечённые очередь и время с текстом исходного поста — защита от
    уверенных ошибок модели (правильный формат, но неверные данные).

    Возвращает (True, '') если извлечённые очередь и все времёна реально
    присутствуют в тексте, иначе (False, 'причина').
    Проверяем типы с окнами — outage и supply (у lifted нет очереди/времени).

    ВНИМАНИЕ: проверка формальная (числа есть в тексте), она НЕ отличает
    отключение от подачи света — за смысл отвечает промпт.
    """
    if extraction.get('type') not in ('outage', 'supply'):
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
                    # С запасом: reasoning-модели (gpt-5-*) тратят часть лимита на
                    # внутренние рассуждения; при малом лимите видимый ответ пустой.
                    'max_tokens': 1500,
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
            choice = payload['choices'][0]
            content = choice['message']['content']
            finish_reason = choice.get('finish_reason')
        except (KeyError, IndexError, TypeError):
            logger.error(f"Неожиданный ответ LLM: {str(payload)[:300]}")
            return None

        data = _parse_json(content)
        result = _validate(data)
        if result is None:
            # finish_reason='length' + пустой content = модель упёрлась в лимит токенов
            logger.error(
                f"LLM вернул невалидную структуру (finish_reason={finish_reason}). "
                f"Ответ: {content[:300]!r}"
            )
        return result

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
