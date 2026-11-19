"""Перевод глифов TTF/OTF в векторные полилинии (траектории пера).

Алгоритм (предсказуемый, без разрывов/наложений по вине кодировок):
  1. Берём glyph по Unicode через cmap (кодировка обрабатывается fontTools корректно).
  2. RecordingPen обходит контуры glyph: moveTo/lineTo/curveTo/qCurveTo/closePath.
  3. Кривые сглаживаются в отрезки (flattening) — перо идёт по кратким линейным
     сегментах, что удобно для G-code.
  4. Каждый замкнутый контур glyph = отдельный штрих: перо опускается на первой
     точке, ведётся по контуру, поднимается в конце. Переход между буквами/штрихами
     — перо поднято (G-code генератор поднимает перо между Stroke). Это даёт
     предсказуемый «контурный» почерк, близкий к рукописному: реальная толщина
     штриха шрифта сохраняется.
  5. Применяются размер шрифта (мм), наклон (наклон по X от Y), интервал между
     буквами (tracking) и avance-width — курсор продвигается на ширину glyph +
     tracking.

Для «однолинейной» записи нужен medianaxis/скелет (менее предсказуемо); контурный
режим выбран по умолчанию как детерминированный и надёжный для кириллицы.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen

from backend.config import CONFIG


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Stroke:
    """Один штрих пера. pen_down=True — рисуем контур, False — служебный переезд."""
    points: List[Point]
    pen_down: bool = True


@dataclass
class GlyphPath:
    char: str
    codepoint: int
    strokes: List[Stroke] = field(default_factory=list)
    advance: float = 0.0  # ширинаoglyfa в font units (до scaling)
    drawn: bool = False


def _sample_cubic(p0, p1, p2, p3, n: int = 12) -> List[Tuple[float, float]]:
    """Равномерная семпляция кубической кривой Безье p0->p3 с contrôлями p1, p2."""
    pts: List[Tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        it = 1.0 - t
        x = (it ** 3) * p0[0] + 3 * (it ** 2) * t * p1[0] + 3 * it * (t ** 2) * p2[0] + (t ** 3) * p3[0]
        y = (it ** 3) * p0[1] + 3 * (it ** 2) * t * p1[1] + 3 * it * (t ** 2) * p2[1] + (t ** 3) * p3[1]
        pts.append((x, y))
    return pts


def _sample_quadratic(p0, p1, p2, n: int = 12) -> List[Tuple[float, float]]:
    """Равномерная семпляция квадратичной кривой p0->p2 с contrôle p1."""
    pts: List[Tuple[float, float]] = []
    for i in range(n + 1):
        t = i / n
        it = 1.0 - t
        x = (it ** 2) * p0[0] + 2 * it * t * p1[0] + (t ** 2) * p2[0]
        y = (it ** 2) * p0[1] + 2 * it * t * p1[1] + (t ** 2) * p2[1]
        pts.append((x, y))
    return pts


def _points_from_pen(pen: RecordingPen) -> List[List[Point]]:
    """Преобразует записи RecordingPen в список контуров (списков точек в font units)."""
    contours: List[List[Point]] = []
    current: List[Point] = []
    open_path = False
    SAMPLES = 12

    def ensure_start(pt):
        nonlocal current, open_path
        if not open_path:
            current = [Point(pt[0], pt[1])]
            open_path = True
        else:
            current.append(Point(pt[0], pt[1]))

    for entry in pen.value:
        kind = entry[0]
        args = entry[1]  # RecordingPen хранит аргументы вызова в entry[1] (кортеж)

        if kind == "moveTo":
            if open_path and current:
                contours.append(current)
            pt = args[0]
            current = [Point(pt[0], pt[1])]
            open_path = True

        elif kind == "lineTo":
            if not open_path:
                current = [Point(args[0][0], args[0][1])]
                open_path = True
            else:
                current.append(Point(args[0][0], args[0][1]))

        elif kind == "curveTo":
            # fontTools Pen: curveTo(c1, c2, end) — кубическая Безье.
            pts = list(args)
            if len(pts) == 1:
                ensure_start(pts[0])
            elif len(pts) == 3:
                if not open_path:
                    current = [Point(pts[0][0], pts[0][1])]
                    open_path = True
                p0 = (current[-1].x, current[-1].y)
                for (x, y) in _sample_cubic(p0, pts[0], pts[1], pts[2], SAMPLES)[1:]:
                    current.append(Point(x, y))
                open_path = True
            else:
                # защита от необычного количества точек: соединяем отрезками
                if not open_path:
                    current = [Point(pts[0][0], pts[0][1])]
                    open_path = True
                for pt in pts[1:]:
                    current.append(Point(pt[0], pt[1]))

        elif kind == "qCurveTo":
            # TrueType квадратичная: все точки кроме последней — управление (off-curve),
            # последняя — точка на контуре. Между соседними управлениями подразумевается
            # implied on-curve точка (середина).
            pts = list(args)
            if not pts:
                continue
            if len(pts) == 1:
                ensure_pt(pts[0])
                continue
            if not open_path:
                current = [Point(pts[0][0], pts[0][1])]
                open_path = True
            p0 = (current[-1].x, current[-1].y)
            off = [(pt[0], pt[1]) for pt in pts[:-1]]
            end = (pts[-1][0], pts[-1][1])
            buf = [p0] + off
            n_buf = len(buf)
            for i in range(n_buf - 1):
                c1 = buf[i]
                c2 = buf[i + 1]
                if i + 2 < n_buf:
                    end_pt = ((c2[0] + buf[i + 2][0]) / 2.0, (c2[1] + buf[i + 2][1]) / 2.0)
                else:
                    end_pt = end
                for (x, y) in _sample_quadratic(c1, c2, end_pt, SAMPLES)[1:]:
                    current.append(Point(x, y))
            open_path = True

        elif kind == "closePath":
            if current and len(current) > 1:
                current.append(Point(current[0].x, current[0].y))  # замкнуть контур
            if current:
                contours.append(current)
            current = []
            open_path = False

    if open_path and current:
        contours.append(current)
    return contours


class GlyphEngine:
    def __init__(self, font_path: str,
                 font_size_mm: float = CONFIG.default_font_size_mm,
                 tracking_mm: float = CONFIG.default_tracking_mm,
                 slant_deg: float = CONFIG.default_slant_deg):
        self.font_path = font_path
        self.font_size_mm = font_size_mm
        self.tracking_mm = tracking_mm
        self.slant_rad = math.radians(slant_deg)
        self.font = TTFont(font_path)
        self.glyf = self.font["glyf"]
        self.cmap = self.font.getBestCmap()
        self.units_per_em = int(self.font["head"].unitsPerEm)
        self.scale = font_size_mm / self.units_per_em  # font units -> мм
        self.hmtx = self.font["hmtx"]
        self._cache: Dict[int, GlyphPath] = {}

    def get_glyph_path(self, char: str) -> GlyphPath:
        cp = ord(char)
        if cp in self._cache:
            return self._cache[cp]

        path = GlyphPath(char=char, codepoint=cp)
        glyph_name = self.cmap.get(cp)
        if glyph_name is None:
            path.advance = self._advance_for_char(char)
            self._cache[cp] = path
            return path

        try:
            glyph = self.glyf[glyph_name]
        except Exception:
            path.advance = self._advance_for_glyph_name(glyph_name)
            self._cache[cp] = path
            return path

        pen = RecordingPen()
        glyph.draw(pen, self.glyf)
        contours = _points_from_pen(pen)
        strokes: List[Stroke] = []
        for contour in contours:
            cleaned = self._clean_contour(contour)
            if len(cleaned) >= 2:
                strokes.append(Stroke(points=cleaned, pen_down=True))
        path.strokes = strokes
        path.advance = self._advance_for_glyph_name(glyph_name)
        path.drawn = len(path.strokes) > 0
        self._cache[cp] = path
        return path

    def _advance_for_char(self, char: str) -> float:
        cp = ord(char)
        name = self.cmap.get(cp)
        if name is None:
            return 0.5 * self.units_per_em
        return self._advance_for_glyph_name(name)

    def _advance_for_glyph_name(self, name: str) -> float:
        try:
            return self.hmtx[name][0]
        except Exception:
            return 0.6 * self.units_per_em

    @staticmethod
    def _clean_contour(contour: List[Point]) -> List[Point]:
        cleaned: List[Point] = []
        for p in contour:
            if cleaned and math.hypot(p.x - cleaned[-1].x, p.y - cleaned[-1].y) < 0.001:
                continue
            cleaned.append(p)
        if len(cleaned) > 1 and math.hypot(cleaned[0].x - cleaned[-1].x,
                                            cleaned[0].y - cleaned[-1].y) < 0.001:
            cleaned.pop()
        return cleaned

    def transform_points(self, points: List[Point], offset_x: float = 0.0,
                         offset_y: float = 0.0) -> List[Tuple[float, float]]:
        """Масштабирование font units -> мм, наклон (shear X от Y), смещение (мм)."""
        out: List[Tuple[float, float]] = []
        tan_slant = math.tan(self.slant_rad)
        for p in points:
            x = p.x * self.scale + offset_x
            y = p.y * self.scale + offset_y
            if abs(tan_slant) > 1e-9:
                x = x + y * tan_slant
            out.append((round(x, 4), round(y, 4)))
        return out

    def advance_mm(self, char: str) -> float:
        return self.get_glyph_path(char).advance * self.scale + self.tracking_mm

    def space_width_mm(self) -> float:
        return self._advance_for_char(" ") * self.scale + self.tracking_mm


def text_to_strokes(
    text: str,
    font_path: str,
    font_size_mm: float = CONFIG.default_font_size_mm,
    tracking_mm: float = CONFIG.default_tracking_mm,
    slant_deg: float = CONFIG.default_slant_deg,
    start_x: float = 0.0,
    start_y: float = 0.0,
) -> Tuple[List[Stroke], float, float]:
    """Перевести строку (без внутренних переносов) в список штрихов.

    Возвращает (strokes, end_x, end_y) в мм относительно (start_x, start_y).
    Между контурами одной буквы и между буквами перо поднимается (обрабатывается
    на стороне gcodegen: переход с pen_up из конца одного Stroke в начало следующего).
    """
    engine = GlyphEngine(font_path, font_size_mm=font_size_mm,
                         tracking_mm=tracking_mm, slant_deg=slant_deg)
    strokes: List[Stroke] = []
    cx = start_x
    cy = start_y
    for ch in text:
        if ch == "\n":
            continue  # перенос строки обращается вызывающим кодом
        if ch == " ":
            cx += engine.space_width_mm()
            continue
        path = engine.get_glyph_path(ch)
        if not path.drawn:
            cx += path.advance * engine.scale + tracking_mm
            continue
        for stroke in path.strokes:
            tpts = engine.transform_points(stroke.points, offset_x=cx, offset_y=cy)
            strokes.append(Stroke(points=[Point(x, y) for (x, y) in tpts], pen_down=True))
        cx += path.advance * engine.scale + tracking_mm
    return strokes, cx, cy
