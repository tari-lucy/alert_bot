"""
Состояние сигналов оповещения — защита от повторной публикации одного и того же.

Зачем не хватило дедупа по времени. Один сигнал приходит теперь из двух
источников: в ВК он появляется раньше, в телеграме позже, разбег — минуты.
Прежнее окно в 180 секунд такой повтор уже не ловило, а просто расширить его
нельзя: тогда настоящая повторная тревога, объявленная вскоре после отбоя,
была бы проглочена как дубль.

Поэтому помним СОСТОЯНИЕ каждой группы сигналов: «air» — воздушная тревога,
«sea» — морская опасность. Публикуем только переход: тревога после отбоя или
отбой после тревоги. Повтор того же сигнала — из любого источника, в любом
виде (текстом или картинкой) — гасится независимо от разбега во времени.

Группы независимы: морской отбой не отменяет воздушную тревогу.

У состояния есть срок годности (TTL). Это принципиально: если отбой мы
пропустим (например, он выйдет плашкой, которую не удалось распознать),
состояние навсегда осталось бы «тревога» и заглушило бы СЛЕДУЮЩУЮ тревогу.
Молчание опаснее лишнего сообщения, поэтому по истечении TTL состояние
считается неизвестным и сигнал публикуется.

Состояние переживает перезапуск: иначе рестарт в момент, когда второй
источник ещё не отдал свою копию сигнала, приводил бы к дублю.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiofiles

from .logger import setup_logger

logger = setup_logger('signal_state')


class SignalState:
    def __init__(self, file_path: str = 'data/signal_state.json', ttl_seconds: int = 1800):
        self.file_path = file_path
        self.ttl = timedelta(seconds=ttl_seconds)
        # группа -> {'kind': 'alert'|'clear', 'at': datetime}
        self._state = {}
        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    async def load(self):
        self._state = {}
        if not os.path.exists(self.file_path):
            return
        try:
            async with aiofiles.open(self.file_path, 'r', encoding='utf-8') as f:
                raw = json.loads(await f.read())
            for group, item in (raw or {}).items():
                at = datetime.fromisoformat(item['at'])
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                self._state[group] = {'kind': item['kind'], 'at': at}
            logger.info(f"Загружено состояние сигналов: {self.describe()}")
        except Exception as e:
            logger.error(f"Не удалось загрузить состояние сигналов: {e}")
            self._state = {}

    async def _save(self):
        try:
            raw = {
                group: {'kind': item['kind'], 'at': item['at'].isoformat()}
                for group, item in self._state.items()
            }
            async with aiofiles.open(self.file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(raw, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Не удалось сохранить состояние сигналов: {e}")

    def current(self, group: str) -> Optional[str]:
        """Действующий вид сигнала группы или None (неизвестно/просрочено)."""
        item = self._state.get(group)
        if not item:
            return None
        if datetime.now(timezone.utc) - item['at'] > self.ttl:
            return None
        return item['kind']

    def is_duplicate(self, group: str, kind: str) -> bool:
        """
        True, если такой сигнал в этой группе уже действует.

        Именно здесь гасится вторая копия сигнала, пришедшая из другого
        источника, — независимо от того, текстом она пришла или картинкой.
        """
        return self.current(group) == kind

    async def remember(self, group: str, kind: str):
        self._state[group] = {'kind': kind, 'at': datetime.now(timezone.utc)}
        await self._save()

    def describe(self) -> str:
        if not self._state:
            return "пусто"
        parts = []
        for group in sorted(self._state):
            actual = self.current(group)
            parts.append(f"{group}={actual or 'неизвестно (просрочено)'}")
        return ", ".join(parts)
