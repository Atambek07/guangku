"""Разметка страниц: тетрадь в клетку / в линейку / чистый лист / A4.

Реализует перенос слов, абзацы, красную строку, поля, подсчёт страниц и
формирование последовательности штрихов (Stroke) для последующей генерации G-code.
Координаты в мм, начало (0,0) — левый верхний угол рабочей зоны листа, Y вниз.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from backend.config import CONFIG
from backend.glyphs import Stroke, text_to_strokes


@dataclass
class Preset:
    id: str
    name: str
    kind: str  # grid | lined | blank | a4
    work_width_mm: float = CONFIG.machine.work_width_mm
    work_height_mm: float = CONFIG.machine.work_height_mm
    margin_left_mm: float = 10.0
    margin_right_mm: float = 10.0
    margin_top_mm: float = 10.0
    margin_bottom_mm: float = 10.0
    # Для клетки:
    cell_size_mm: float = 5.0
    # Для линейки:
    line_spacing_mm: float = 8.0
    # Общее шрифтовое оформление:
    font_size_mm: float = CONFIG.default_font_size_mm
    line_height_mm: float = CONFIG.default_line_height_mm
    tracking_mm: float = CONFIG.default_tracking_mm
    word_spacing_mm: float = CONFIG.default_word_spacing_mm
    slant_deg: float = CONFIG.default_slant_deg
    red_line_indent_mm: float = 5.0  # отступ первой строки абзаца
    font_name: str = ""


@dataclass
class Page:
    page_no: int
    width_mm: float
    height_mm: float
    strokes: List[Stroke] = field(default_factory=list)
    char_count: int = 0


@dataclass
class LayoutResult:
    preset: Preset
    pages: List[Page]
    page_count: int
    total_chars: int
    total_strokes: int
    est_time_min: float
    preview_svg: str


def load_presets(presets_dir: str = "presets") -> List[Preset]:
    """Встроенные пресеты + опциональные JSON-файлы из папки presets/."""
    presets: List[Preset] = [
        Preset(
            id="notebook_grid_5mm",
            name="Тетрадь в клетку (5×5 мм)",
            kind="grid",
            cell_size_mm=5.0,
            font_size_mm=6.0,
            line_height_mm=5.0,
            margin_left_mm=8.0,
            margin_right_mm=8.0,
            margin_top_mm=8.0,
            margin_bottom_mm=8.0,
            tracking_mm=0.8,
            red_line_indent_mm=5.0,
        ),
        Preset(
            id="notebook_grid_7mm",
            name="Тетрадь в клетку (7×7 мм)",
            kind="grid",
            cell_size_mm=7.0,
            font_size_mm=8.0,
            line_height_mm=7.0,
            margin_left_mm=10.0,
            margin_right_mm=10.0,
            margin_top_mm=10.0,
            margin_bottom_mm=10.0,
            tracking_mm=1.0,
            red_line_indent_mm=7.0,
        ),
        Preset(
            id="notebook_lined",
            name="Тетрадь в линейку",
            kind="lined",
            line_spacing_mm=10.0,
            font_size_mm=7.0,
            line_height_mm=10.0,
            margin_left_mm=10.0,
            margin_right_mm=10.0,
            margin_top_mm=12.0,
            margin_bottom_mm=12.0,
            tracking_mm=1.0,
            red_line_indent_mm=8.0,
        ),
        Preset(
            id="notebook_blank",
            name="Чистый лист тетради",
            kind="blank",
            font_size_mm=8.0,
            line_height_mm=14.0,
            margin_left_mm=10.0,
            margin_right_mm=10.0,
            margin_top_mm=12.0,
            margin_bottom_mm=12.0,
            tracking_mm=1.0,
            red_line_indent_mm=10.0,
        ),
        Preset(
            id="a4",
            name="Лист A4 (210×297 мм)",
            kind="a4",
            work_width_mm=210.0,
            work_height_mm=297.0,
            font_size_mm=10.0,
            line_height_mm=15.0,
            margin_left_mm=15.0,
            margin_right_mm=15.0,
            margin_top_mm=15.0,
            margin_bottom_mm=15.0,
            tracking_mm=1.2,
            red_line_indent_mm=10.0,
        ),
    ]
    # Дополнительные JSON-пресеты из папки (опционально)
    import os, json
    if os.path.isdir(presets_dir):
        for fn in sorted(os.listdir(presets_dir)):
            if not fn.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(presets_dir, fn), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                presets.append(Preset(**data))
            except Exception:
                continue
    return presets


def _wrap_words(words: List[str], font_engine_get_width, max_width_mm: float,
                first_indent_mm: float) -> List[List[str]]:
    """Разбить список слов на строки по ширине (мм) с учётом отступа красной строки."""
    lines: List[List[str]] = []
    current: List[str] = []
    current_width = 0.0
    indent = first_indent_mm
    space_w = font_engine_get_width(" ") if font_engine_get_width else 2.0

    def word_width(w: str) -> float:
        # примерная ширина слова = сумма avance (без точного рендера)
        return sum(font_engine_get_width(ch) if font_engine_get_width else len(ch) * 1.0
                   for ch in w)

    for w in words:
        ww = word_width(w)
        add = ww if not current else ww + space_w
        need = indent + current_width + add
        if current and need > max_width_mm:
            lines.append(current)
            current = [w]
            current_width = ww
            indent = 0.0
        else:
            current.append(w)
            current_width += add
    if current:
        lines.append(current)
    return lines


class LayoutEngine:
    def __init__(self, preset: Preset, font_path: str):
        self.preset = preset
        self.font_path = font_path
        self.p = preset

    def _available_width(self) -> float:
        return (self.p.work_width_mm - self.p.margin_left_mm - self.p.margin_right_mm)

    def _available_height(self) -> float:
        return (self.p.work_height_mm - self.p.margin_top_mm - self.p.margin_bottom_mm)

    def layout(self, text: str) -> LayoutResult:
        p = self.p
        max_w = self._available_width()
        max_h = self._available_height()

        # Разбиваем текст на абзацы по пустым строкам и одинарным переносам.
        # Каждый одинарный \n = новая строка текста (абзац из одной строки).
        raw_paragraphs = text.split("\n")
        paragraphs: List[List[str]] = []  # список абзацев, каждый = список слов
        for para in raw_paragraphs:
            words = [w for w in para.split(" ") if w != ""]
            paragraphs.append(words)
        # если текст пустой — один пустой абзац
        if not paragraphs:
            paragraphs = [[]]

        # Подготовим двигатель шрифта для примерных ширин (advance)
        from backend.glyphs import GlyphEngine
        engine = GlyphEngine(self.font_path,
                             font_size_mm=p.font_size_mm,
                             tracking_mm=p.tracking_mm,
                             slant_deg=p.slant_deg)
        get_width = lambda ch: engine.advance_mm(ch) - engine.tracking_mm

        pages: List[Page] = []
        page_no = 1
        cur_page = Page(page_no=page_no,
                        width_mm=p.work_width_mm, height_mm=p.work_height_mm)
        pages.append(cur_page)

        # Вертикальный курсор (мм) и номер текущей строки на странице
        y = p.margin_top_mm
        line_index = 0  # индекс строки на странице (для сетки/линейки)

        def line_y_for_index(idx: int) -> float:
            if p.kind == "grid":
                return p.margin_top_mm + idx * p.cell_size_mm
            if p.kind == "lined":
                return p.margin_top_mm + idx * p.line_spacing_mm
            # blank / a4: непрерывное ведение курсора
            return p.margin_top_mm + idx * p.line_height_mm

        def needs_new_page(next_y: float) -> bool:
            return next_y + p.font_size_mm > p.margin_top_mm + max_h

        def flush_line(words_line: List[str], y_line: float, first_indent: bool,
                       char_counter: List[int]) -> Tuple[float, int]:
            """Нарисовать одну строку слов на высоте y_line. Вернуть (y_line, chars)."""
            x = p.margin_left_mm + (p.red_line_indent_mm if first_indent else 0.0)
            chars = 0
            for wi, w in enumerate(words_line):
                # штрихи слова
                word_strokes, _, _ = text_to_strokes(
                    w, self.font_path,
                    font_size_mm=p.font_size_mm,
                    tracking_mm=p.tracking_mm,
                    slant_deg=p.slant_deg,
                    start_x=x, start_y=y_line,
                )
                cur_page.strokes.extend(word_strokes)
                chars += len(w)
                # сдвиг курсора на ширину слова + пробел (кроме последнего)
                x += sum(engine.advance_mm(ch) for ch in w)
                if wi < len(words_line) - 1:
                    x += engine.space_width_mm()
            return x, chars

        char_counter_total = 0

        for para_idx, words in enumerate(paragraphs):
            first_indent = (para_idx == 0) or (para_idx > 0 and
                                               paragraphs[para_idx - 1] == [])
            # красная строка только для первой строки абзаца
            is_first_line_of_para = True
            line_words_pool = list(words)
            while line_words_pool:
                # определяем, сколько слов влезает в строку
                # простой жадный подбор
                line: List[str] = []
                line_w = 0.0
                indent = p.red_line_indent_mm if is_first_line_of_para else 0.0
                space_w = engine.space_width_mm()
                for w in line_words_pool:
                    ww = sum(engine.advance_mm(ch) for ch in w)
                    add = ww if not line else ww + space_w
                    if line and (p.margin_left_mm + indent + line_w + add >
                                 p.margin_left_mm + max_w):
                        break
                    line.append(w)
                    line_w += add
                if not line:
                    # слово не влезло целиком — переносим по буквам (разбиваем слово)
                    w = line_words_pool.pop(0)
                    # рисуем что влезло по буквам
                    part = ""
                    for ch in w:
                        ww = engine.advance_mm(ch)
                        if part and (p.margin_left_mm + indent + line_w + ww >
                                     p.margin_left_mm + max_w):
                            break
                        part += ch
                        line_w += ww
                    if part:
                        word_strokes, _, _ = text_to_strokes(
                            part, self.font_path,
                            font_size_mm=p.font_size_mm,
                            tracking_mm=p.tracking_mm,
                            slant_deg=p.slant_deg,
                            start_x=p.margin_left_mm + indent,
                            start_y=y,
                        )
                        cur_page.strokes.extend(word_strokes)
                        char_counter_total += len(part)
                    # остаток слова возвращаем в начало очереди
                    rest = w[len(part):]
                    line_words_pool.insert(0, rest)
                    # перевод строки
                    is_first_line_of_para = False
                    y = line_y_for_index(line_index + 1)
                    line_index += 1
                    if needs_new_page(y + p.line_height_mm):
                        page_no += 1
                        cur_page = Page(page_no=page_no,
                                        width_mm=p.work_width_mm,
                                        height_mm=p.work_height_mm)
                        pages.append(cur_page)
                        y = line_y_for_index(0)
                        line_index = 0
                    continue

                # удаляем использованные слова из пула
                line_words_pool = line_words_pool[len(line):]
                y_line = line_y_for_index(line_index)
                if needs_new_page(y_line + p.line_height_mm):
                    page_no += 1
                    cur_page = Page(page_no=page_no,
                                    width_mm=p.work_width_mm,
                                    height_mm=p.work_height_mm)
                    pages.append(cur_page)
                    y = line_y_for_index(0)
                    line_index = 0
                    y_line = line_y_for_index(line_index)

                _, chars = flush_line(line, y_line, is_first_line_of_para,
                                      [char_counter_total])
                char_counter_total += chars
                is_first_line_of_para = False

                # перевод строки
                y = line_y_for_index(line_index + 1)
                line_index += 1
                if needs_new_page(y + p.line_height_mm):
                    page_no += 1
                    cur_page = Page(page_no=page_no,
                                    width_mm=p.work_width_mm,
                                    height_mm=p.work_height_mm)
                    pages.append(cur_page)
                    y = line_y_for_index(0)
                    line_index = 0

        # Подсчёт итогов
        total_strokes = sum(len(pg.strokes) for pg in pages)
        total_chars = char_counter_total
        est_len_mm = sum(
            sum(math.hypot(b.x - a.x, b.y - a.y)
                for i in range(1, len(s.points)) for a, b in [(s.points[i - 1], s.points[i])])
            for pg in pages for s in pg.strokes if s.pen_down
        )
        write_feed = CONFIG.feed.write  # мм/мин
        est_time_min = round(est_len_mm / write_feed + total_strokes * 0.5 / 60.0, 2)

        preview_svg = self._preview_svg(pages)

        return LayoutResult(
            preset=p,
            pages=pages,
            page_count=len(pages),
            total_chars=total_chars,
            total_strokes=total_strokes,
            est_time_min=est_time_min,
            preview_svg=preview_svg,
        )

    def _preview_svg(self, pages: List[Page]) -> str:
        """SVG-превью всех страниц (для вставки в HTML-канвас/тег)."""
        parts: List[str] = []
        scale = 1.0
        for pg in pages:
            parts.append(
                f'<g id="page{pg.page_no}" transform="translate(0,{ (pg.page_no-1) * 10 })">'
            )
            parts.append(
                f'<rect x="{pg.width_mm}" y="0" width="{pg.height_mm}" '
                f'height="{pg.height_mm}" fill="none" stroke="#ddd" stroke-width="0.5"/>'
            )
            # сетка/разметка под preset
            if self.p.kind == "grid":
                cs = self.p.cell_size_mm
                for gx in range(int(self.p.margin_left_mm), int(pg.width_mm - self.p.margin_right_mm), int(cs)):
                    parts.append(f'<line x1="{gx}" y1="{self.p.margin_top_mm}" x2="{gx}" y2="{pg.height_mm - self.p.margin_bottom_mm}" stroke="#eee" stroke-width="0.3"/>')
                for gy in range(int(self.p.margin_top_mm), int(pg.height_mm - self.p.margin_bottom_mm), int(cs)):
                    parts.append(f'<line x1="{self.p.margin_left_mm}" y1="{gy}" x2="{pg.width_mm - self.p.margin_right_mm}" y2="{gy}" stroke="#eee" stroke-width="0.3"/>')
            for s in pg.strokes:
                pts = " ".join(f"{pt.x},{pt.y}" for pt in s.points)
                if len(s.points) < 2:
                    continue
                stroke_col = "#222222" if s.pen_down else "#999999"
                parts.append(
                    f'<polyline points="{pts}" fill="none" stroke="{stroke_col}" '
                    f'stroke-width="{0.18 if s.pen_down else 0.1}" stroke-linecap="round"/>'
                )
            parts.append("</g>")
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{int(pages[0].width_mm * scale) if pages else 210}" '
            f'height="{int(sum(pg.height_mm for pg in pages) * scale) if pages else 300}" '
            f'viewBox="0 0 {pages[0].width_mm if pages else 210} {sum(pg.height_mm for pg in pages) if pages else 300}">'
            + "".join(parts) + "</svg>"
        )
        return svg
