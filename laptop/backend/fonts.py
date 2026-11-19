"""Сканирование папки ./fonts/, проверка поддержки кириллицы и метрики шрифтов."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from fontTools.ttLib import TTFont, TTLibError

from backend.config import FONTS_DIR


CYRILLIC_START = 0x0400
CYRILLIC_END = 0x0500  # не включается
CYRILLIC_COUNT = CYRILLIC_END - CYRILLIC_START  # 256

# Контрольный набор бу ки Cyrillic, обязательных для хорошего конспекта:
CYRILLIC_SAMPLE = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"


@dataclass
class FontInfo:
    path: str
    name: str
    filename: str
    units_per_em: int
    cyrillic_present: int = 0
    cyrillic_total: int = CYRILLIC_COUNT
    cyrillic_coverage: float = 0.0
    sample_ok: bool = False
    missing_sample: List[str] = field(default_factory=list)
    error: Optional[str] = None


def scan_fonts(fonts_dir: str = FONTS_DIR) -> List[FontInfo]:
    """Просканировать папку со шрифтами и вернуть информацию о каждом .ttf/.otf.

    Для каждого файла проверяется cmap-таблица: presence of Cyrillic codepoints
    (U+0400–U+04FF) и контрольный набор букв. Это защищает от ситуации, когда
    пользователь подкладывает латинский шрифт и получает «кубики» вместо кириллицы.
    """
    results: List[FontInfo] = []
    if not os.path.isdir(fonts_dir):
        return results

    for filename in sorted(os.listdir(fonts_dir)):
        if not filename.lower().endswith((".ttf", ".otf", ".ttc")):
            continue
        path = os.path.join(fonts_dir, filename)
        info = FontInfo(
            path=path,
            name=os.path.splitext(filename)[0],
            filename=filename,
            units_per_em=0,
        )
        try:
            font = TTFont(path, fontNumber=0)
            info.units_per_em = int(font["head"].unitsPerEm)
            cmap = font.getBestCmap()
            present = [c for c in range(CYRILLIC_START, CYRILLIC_END) if c in cmap]
            info.cyrillic_present = len(present)
            info.cyrillic_coverage = round(100.0 * len(present) / CYRILLIC_COUNT, 1)
            missing = [ch for ch in CYRILLIC_SAMPLE if ord(ch) not in cmap]
            info.sample_ok = len(missing) == 0
            info.missing_sample = missing
        except TTLibError as exc:
            info.error = str(exc)
        except Exception as exc:  # pragma: no cover - защита от битых файлов
            info.error = f"{type(exc).__name__}: {exc}"
        results.append(info)
    return results


def find_font(fonts_dir: str = FONTS_DIR, name_or_file: str = "") -> Optional[str]:
    """Найти полный путь к шрифту по имени или имени файла."""
    if not name_or_file:
        return None
    for info in scan_fonts(fonts_dir):
        if info.name == name_or_file or info.filename == name_or_file:
            return info.path
    return None


def default_font(fonts_dir: str = FONTS_DIR) -> Optional[FontInfo]:
    """Вернуть первый шрифт с хорошей кириллицей, или первый доступный."""
    infos = scan_fonts(fonts_dir)
    if not infos:
        return None
    good = [i for i in infos if not i.error and i.cyrillic_coverage >= 50]
    return good[0] if good else infos[0]
