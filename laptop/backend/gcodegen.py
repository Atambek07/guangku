"""Генерация G-code для GRBL из последовательности штрихов (Stroke).

Поддержка:
  - подъём/опускание перо (Z-ось или серво/шпиндель, см. config.PenConfig);
  - скорость написания (F write) и перемещения перо поднято (F travel);
  - преобразование СК листа (0,0 = левый верхний угол, Y вниз) в СК станка
    (origin_corner, y_inverted, work_width/height);
  - заголовок (G90/G21/G92) и подвал (поднять перо, уйти в ноль);
  - округление координат до 0.001 мм.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import List, Tuple

from backend.config import CONFIG, GCODE_DIR
from backend.glyphs import Stroke


@dataclass
class GCodeResult:
    gcode: str
    filename: str
    path: str
    line_count: int
    est_time_min: float
    mm_total: float


class GCodeGenerator:
    def __init__(self):
        self.m = CONFIG.machine
        self.p = CONFIG.pen
        self.f = CONFIG.feed

    # ---------- координатные преобразования ----------
    def _machine_xy(self, x: float, y: float) -> Tuple[float, float]:
        """Лист (X вправо, Y вниз, 0,0 = левый верхний угол) -> станок."""
        mx = x
        my = y
        if self.m.origin_corner == "top-left":
            my = self.m.work_height_mm - y if self.m.y_inverted else y
        elif self.m.origin_corner == "bottom-left":
            my = y if self.m.y_inverted else self.m.work_height_mm - y
        elif self.m.origin_corner == "top-right":
            mx = self.m.work_width_mm - x
            my = self.m.work_height_mm - y if self.m.y_inverted else y
        elif self.m.origin_corner == "bottom-right":
            mx = self.m.work_width_mm - x
            my = y if self.m.y_inverted else self.m.work_height_mm - y
        return round(mx, 3), round(my, 3)

    @staticmethod
    def _fmt(v: float) -> str:
        return f"{v:.3f}"

    # ---------- команды перо ----------
    def _pen_down_cmd(self) -> str:
        return self.p.pen_down_cmd.format(
            z_down=self.p.z_down_mm, z_up=self.p.z_up_mm,
            z_feed=self.p.z_feed,
        )

    def _pen_up_cmd(self) -> str:
        return self.p.pen_up_cmd.format(
            z_down=self.p.z_down_mm, z_up=self.p.z_up_mm,
            z_feed=self.p.z_feed,
        )

    # ---------- основная генерация ----------
    def generate(self, pages, filename: str = "output.nc",
                 job_name: str = "plot") -> GCodeResult:
        """Собрать весь G-code для списка Page (или одного списка Stroke)."""
        all_strokes: List[Stroke] = []
        if pages is None:
            pages = []
        if hasattr(pages, "__iter__") and not isinstance(pages, (str, bytes)):
            try:
                first_item = next(iter(pages), None)
            except StopIteration:
                first_item = None
            if first_item is not None and hasattr(first_item, "strokes"):
                for pg in pages:
                    all_strokes.extend(pg.strokes)
            else:
                all_strokes = list(pages)

        lines: List[str] = []
        # ---------- заголовок ----------
        lines.append(f"; {job_name} — сгенерировано плоттер-комплексом")
        lines.append("; units: mm, absolute coordinates")
        lines.append("G21          ; мм")
        lines.append("G90          ; абсолютные координаты")
        lines.append("G94          ; подача в мм/мин")
        lines.append(f"G1 Z{self._fmt(self.p.z_up_mm)} F{self.f.z}   ; перо поднято (старт)")
        lines.append(f"G1 X{self._fmt(0.0)} Y{self._fmt(0.0)} F{self.f.travel}  ; в ноль рабочей зоны")
        lines.append("")

        pen_is_down = False
        first_stroke = True

        for stroke in all_strokes:
            if not stroke.points:
                continue
            want_down = stroke.pen_down
            if want_down and not pen_is_down:
                lines.append(self._pen_down_cmd())
                pen_is_down = True
            elif not want_down and pen_is_down:
                lines.append(self._pen_up_cmd())
                pen_is_down = False

            pts = [self._machine_xy(pt.x, pt.y) for pt in stroke.points]
            if want_down:
                # рисуем: к первой точке опущенным пером, затем по контуру
                lines.append(f"G1 X{self._fmt(pts[0][0])} Y{self._fmt(pts[0][1])} F{self.f.write}")
                for pt in pts[1:]:
                    lines.append(f"G1 X{self._fmt(pt[0])} Y{self._fmt(pt[1])} F{self.f.write}")
            else:
                # перо поднято: быстрый переезд
                lines.append(f"G0 X{self._fmt(pts[0][0])} Y{self._fmt(pts[0][1])} F{self.f.travel}")
            first_stroke = False

        # ---------- подвал ----------
        lines.append(self._pen_up_cmd())  # перо вверх в конце
        lines.append(f"G1 X{self._fmt(self.m.zero_offset_x)} Y{self._fmt(self.m.zero_offset_y)} F{self.f.travel}  ; вернуть перо в ноль")
        lines.append("M2                     ; конец программы")
        lines.append("")

        gcode = "\n".join(lines)
        mm_total = self._mm_length(all_strokes)
        est = self._estimate_minutes(mm_total, len(all_strokes))

        os.makedirs(GCODE_DIR, exist_ok=True)
        path = os.path.join(GCODE_DIR, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(gcode)

        return GCodeResult(
            gcode=gcode,
            filename=filename,
            path=path,
            line_count=len(lines),
            est_time_min=est,
            mm_total=round(mm_total, 2),
        )

    @staticmethod
    def _mm_length(strokes: List[Stroke]) -> float:
        total = 0.0
        for s in strokes:
            if not s.pen_down or len(s.points) < 2:
                continue
            for i in range(1, len(s.points)):
                a, b = s.points[i - 1], s.points[i]
                total += math.hypot(b.x - a.x, b.y - a.y)
        return total

    @staticmethod
    def _estimate_minutes(mm_total: float, strokes: int) -> float:
        from backend.config import CONFIG
        write = CONFIG.feed.write
        travel_extra = strokes * 0.5  # подъёмы/переезды (минуты)
        return round(mm_total / write + travel_extra / 60.0, 2)


def svg_to_strokes(svg_path: str) -> List[Stroke]:
    """Интерфейс к импорту SVG (реализовано в svg_import.py)."""
    from backend.svg_import import svg_to_strokes as _impl
    return _impl(svg_path)
