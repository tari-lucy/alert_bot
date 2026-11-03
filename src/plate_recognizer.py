"""
Распознавание картинок-плашек, которыми источник публикует сигналы оповещения.

С августа 2026 Развожаев публикует часть тревог и отбоев не текстом, а
готовой картинкой без подписи («ВНИМАНИЕ! ВОЗДУШНАЯ ТРЕВОГА!» на красном,
«ОТБОЙ ВОЗДУШНОЙ ТРЕВОГИ!» на зелёном). Пост при этом пустой, и фильтр по
тексту такие сигналы не видит вовсе.

Распознавание двухступенчатое:
  1) перцептивный хеш (dHash) против эталонов из data/plates — мгновенно и
     без сети, покрывает штатный случай;
  2) если картинка незнакомая — её отдают распознавателю текста (зрение LLM),
     и уже распознанный текст идёт в обычный TextFilter.

Ключом эталона НЕ может быть ни photo_id, ни md5: источник периодически
перезаливает тот же файл, получая новый id, а при пережатии меняются и байты
(в одной неделе наблюдений уже по два байтовых варианта каждой плашки).
Перцептивный хеш переживает и перезаливку, и смену размера, и пережатие.

Возвращается всегда ТЕКСТ плашки, а не тип сигнала: решение о типе принимает
тот же TextFilter по файлам ключевых фраз, что и для текстовых постов. Так
списки фраз остаются единственным источником правды.
"""
import io
import json
import os
from datetime import datetime

from PIL import Image

from .logger import setup_logger

logger = setup_logger('plate_recognizer')

# Размер сетки dHash: 8x8 = 64 бита. Сравниваем соседние пиксели по строке,
# поэтому картинку уменьшаем до (SIZE+1) x SIZE.
HASH_SIZE = 8

# Максимальное расстояние Хэмминга, при котором картинки считаются одной и той
# же. 0 — побайтово одинаковый рендер, 10 — уже разные изображения. Порог 8
# проверен на реальных перезаливках плашек: свои варианты дают 0-6, любые
# посторонние фото со стены — 20 и больше.
MAX_DISTANCE = 8

MANIFEST_NAME = 'plates.json'


def dhash(image: Image.Image, size: int = HASH_SIZE) -> int:
    """
    Перцептивный хеш: яркость соседних пикселей уменьшенной картинки.

    Устойчив к пережатию, смене размера и лёгкой правке цвета — то есть ровно
    к тому, что делает с плашкой перезаливка через редактор.
    """
    small = image.convert('L').resize((size + 1, size), Image.LANCZOS)
    px = small.load()
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | int(px[col, row] > px[col + 1, row])
    return bits


def hamming(a: int, b: int) -> int:
    """Число различающихся бит двух хешей."""
    return bin(a ^ b).count('1')


class PlateRecognizer:
    """
    Сопоставляет картинку с эталонными плашками и возвращает их текст.

    Args:
        plates_dir: каталог с эталонами и манифестом plates.json
        vision: необязательный распознаватель незнакомых картинок с методом
            `async recognize(image_bytes) -> str | None` (зрение LLM). Без него
            работает только сверка с эталонами.
    """

    def __init__(self, plates_dir: str = 'data/plates', vision=None):
        self.plates_dir = plates_dir
        self.vision = vision
        # [(хеш, текст плашки, имя файла)]
        self._plates = []

    def load_plates(self) -> int:
        """
        Загружает эталоны из манифеста. Возвращает их количество.

        Манифест — список объектов {"file": "...", "text": "..."}: текст пишем
        ровно так, как он звучал бы в подписи поста, чтобы его можно было
        отдать в TextFilter наравне с настоящим текстом.
        """
        self._plates = []
        manifest_path = os.path.join(self.plates_dir, MANIFEST_NAME)
        if not os.path.exists(manifest_path):
            logger.warning(f"Манифест плашек не найден: {manifest_path}")
            return 0

        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                entries = json.load(f)
        except Exception as e:
            logger.error(f"Не удалось прочитать манифест плашек: {e}")
            return 0

        for entry in entries:
            path = os.path.join(self.plates_dir, entry.get('file', ''))
            text = (entry.get('text') or '').strip()
            if not text or not os.path.exists(path):
                logger.error(f"Эталон пропущен (нет файла или текста): {entry}")
                continue
            try:
                with Image.open(path) as img:
                    self._plates.append((dhash(img), text, entry['file']))
            except Exception as e:
                logger.error(f"Не удалось прочитать эталон {path}: {e}")

        logger.info(f"Загружено эталонов плашек: {len(self._plates)}")
        return len(self._plates)

    def match(self, image_bytes: bytes):
        """
        Текст ближайшего эталона или None, если картинка незнакомая.

        Никаких догадок: если ближе порога ничего нет, честно возвращаем None —
        пусть решает зрение или человек. Ошибиться плашкой хуже, чем не понять.
        """
        if not self._plates:
            return None
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                value = dhash(img)
        except Exception as e:
            logger.error(f"Не удалось разобрать картинку: {e}")
            return None

        best_text, best_file, best_dist = None, None, None
        for plate_hash, text, name in self._plates:
            dist = hamming(value, plate_hash)
            if best_dist is None or dist < best_dist:
                best_text, best_file, best_dist = text, name, dist

        if best_dist is not None and best_dist <= MAX_DISTANCE:
            logger.info(f"Плашка опознана как «{best_text}» (эталон {best_file}, расстояние {best_dist})")
            return best_text

        logger.info(f"Картинка не совпала ни с одним эталоном (ближайшее расстояние {best_dist})")
        return None

    def remember_plate(self, image_bytes: bytes, text: str, prefix: str = 'auto'):
        """
        Добавляет новую плашку в эталоны: файл + строка в манифесте.

        Вызывается ТОЛЬКО когда распознанный зрением текст совпал с известной
        ключевой фразой, то есть картинка заведомо является сигналом. Иначе
        ошибка распознавания осела бы в эталонах навсегда и повторялась бы уже
        без участия модели.

        Возвращает имя файла или None.
        """
        try:
            os.makedirs(self.plates_dir, exist_ok=True)
            with Image.open(io.BytesIO(image_bytes)) as img:
                value = dhash(img)

            # Уже знаем такую — второй раз не сохраняем.
            for plate_hash, _, name in self._plates:
                if hamming(value, plate_hash) <= MAX_DISTANCE:
                    return None

            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'{prefix}_{stamp}.jpg'
            with open(os.path.join(self.plates_dir, filename), 'wb') as f:
                f.write(image_bytes)

            manifest_path = os.path.join(self.plates_dir, MANIFEST_NAME)
            entries = []
            if os.path.exists(manifest_path):
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    entries = json.load(f)
            entries.append({
                'file': filename,
                'text': text,
                'comment': f'добавлено автоматически {stamp} после распознавания зрением',
            })
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)

            self._plates.append((value, text, filename))
            logger.info(f"Новая плашка сохранена в эталоны: {filename} → «{text}»")
            return filename
        except Exception as e:
            logger.error(f"Не удалось сохранить новую плашку: {e}")
            return None

    async def recognize(self, image_bytes: bytes):
        """
        Текст с картинки: сперва эталоны, при промахе — зрение (если задано).

        Возвращает (текст, источник): источник — 'plate' или 'vision', чтобы
        вызывающий код мог по-разному поступать с новой, ещё не виденной
        плашкой (например, уведомить администратора).
        """
        text = self.match(image_bytes)
        if text:
            return text, 'plate'

        if self.vision is None:
            return None, None

        try:
            text = await self.vision.recognize(image_bytes)
        except Exception as e:
            logger.error(f"Ошибка распознавания картинки зрением: {e}")
            return None, None

        if text:
            logger.info(f"Незнакомая картинка распознана зрением: «{text[:80]}»")
            return text, 'vision'
        return None, None
