"""Рендер предпросмотра страниц в PNG (Pillow) для отладки и отдачи через API."""

from __future__ import annotations

import os
from io import BytesIO
from typing import List

from PIL import Image, ImageDraw

from backend.config import OUTPUT_DIR
from backend.layout import Page


def render_pages_png(pages: List[Page], scale: float = 2.0,
                     gap_mm: float = 5.0) -> bytes:
    """Нарисовать все страницы вертикально в одно PNG-изображение.

    Чёрный — штрих письма (перо внизу), серый — переезд перо вверху,
    пунктирная рамка — граница листа.
    """
    if not pages:
        img = Image.new("RGB", (400, 300), "white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    pw = max(pg.width_mm for pg in pages)
    total_h = sum(pg.height_mm for pg in pages) + gap_mm * (len(pages) - 1)
    W = int(pw * scale) + 40
    H = int(total_h * scale) + 40
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    oy = 20
    for pg in pages:
        ox = 20
        # рамка листа
        draw.rectangle(
            [ox, oy, ox + int(pg.width_mm * scale), oy + int(pg.height_mm * scale)],
            outline="#cccccc", width=1,
        )
        # поля
        ml = int(pg.width_mm * 0.1 * scale)  # примерно, если нет preset — 10%
        # штрихи
        for s in pg.strokes:
            pts = [(ox + int(pt.x * scale), oy + int(pt.y * scale)) for pt in s.points]
            if len(pts) < 2:
                continue
            col = "black" if s.pen_down else "#999999"
            width = 2 if s.pen_down else 1
            draw.line(pts, fill=col, width=width, joint="curve")
        # подпись страницы
        draw.text((ox, oy - 14), f"Страница {pg.page_no}", fill="#666666")
        oy += int(pg.height_mm * scale) + int(gap_mm * scale)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
