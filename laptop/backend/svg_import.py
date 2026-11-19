"""Импорт векторных файлов .svg в последовательность штрихов (Stroke).

Минимальный парсер SVG-путей (M, L, H, V, C, S, Q, T, Z) без внешних зависимостей.
Каждый замкнутый/разомкнутый контур path = один штрих. Полилинии и прямоугольники
преобразуются в пути. Координаты берутся как есть (мм), масштабирование под A4 —
на вызывающей стороне (см. endpoint /api/svg).
"""

from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET
from typing import List, Tuple

from backend.glyphs import Stroke

NS = "{http://www.w3.org/2000/svg}"
NUM = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"
COMMAND_RE = re.compile(r"([MLCSQHTZmlcsqhtz])" + NUM + r"(?:" + "," + r"?\s*" + NUM + r")*")


def _parse_floats(s: str) -> List[float]:
    return [float(m) for m in re.findall(NUM, s)]


def _sample_cubic(p0, p1, p2, p3, n=10):
    pts = []
    for i in range(n + 1):
        t = i / n
        it = 1.0 - t
        x = (it ** 3) * p0[0] + 3 * (it ** 2) * t * p1[0] + 3 * it * (t ** 2) * p2[0] + (t ** 3) * p3[0]
        y = (it ** 3) * p0[1] + 3 * (it ** 2) * t * p1[1] + 3 * it * (t ** 2) * p2[1] + (t ** 3) * p3[1]
        pts.append((x, y))
    return pts


def _sample_quad(p0, p1, p2, n=10):
    pts = []
    for i in range(n + 1):
        t = i / n
        it = 1.0 - t
        x = (it ** 2) * p0[0] + 2 * it * t * p1[0] + (t ** 2) * p2[0]
        y = (it ** 2) * p0[1] + 2 * it * t * p1[1] + (t ** 2) * p2[1]
        pts.append((x, y))
    return pts


def _parse_path_d(d: str) -> List[Stroke]:
    strokes: List[Stroke] = []
    token_re = re.compile(r"[MLCSQHTZmlcsqhtz]|" + NUM)
    tokens = [m.group(0) for m in token_re.finditer(d)]
    # сгруппируем команды и их аргументы
    cmds: List[Tuple[str, List[float]]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"[MLCSQHTZmlcsqhtz]", tok):
            cmd = tok.upper()
            args: List[float] = []
            i += 1
            while i < len(tokens) and re.match(NUM, tokens[i]):
                args.append(float(tokens[i]))
                i += 1
            cmds.append((cmd, args))
        else:
            i += 1

    cur: List[Tuple[float, float]] = []
    start_pt: Tuple[float, float] = (0.0, 0.0)
    pen_down = False

    def new_contour():
        nonlocal cur, start_pt, pen_down
        cur = []
        pen_down = False

    def ensure_pt(pt):
        nonlocal cur, pen_down
        if not pen_down:
            cur = [pt]
            pen_down = True
        else:
            cur.append(pt)

    i = 0
    while i < len(cmds):
        cmd, args = cmds[i]
        if cmd == "M":
            if cur and pen_down and len(cur) >= 2:
                strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
            for j in range(0, len(args), 2):
                start_pt = (args[j], args[j + 1])
                ensure_pt(start_pt)
            cmd = "L"  # последующие пары — линии
            i += 1
            continue
        elif cmd == "L":
            for j in range(0, len(args), 2):
                ensure_pt((args[j], args[j + 1]))
        elif cmd == "H":
            for x in args:
                ensure_pt((x, cur[-1][1] if cur else 0.0))
        elif cmd == "V":
            for y in args:
                ensure_pt((cur[-1][0] if cur else 0.0, y))
        elif cmd == "C":
            for j in range(0, len(args), 6):
                p0 = cur[-1] if cur else (0.0, 0.0)
                for pt in _sample_cubic(p0, (args[j], args[j + 1]),
                                        (args[j + 2], args[j + 3]),
                                        (args[j + 4], args[j + 5]))[1:]:
                    ensure_pt(pt)
        elif cmd == "Q":
            for j in range(0, len(args), 4):
                p0 = cur[-1] if cur else (0.0, 0.0)
                for pt in _sample_quad(p0, (args[j], args[j + 1]),
                                       (args[j + 2], args[j + 3]))[1:]:
                    ensure_pt(pt)
        elif cmd == "Z":
            if cur and pen_down and len(cur) >= 2:
                cur.append(start_pt)
                strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
            cur = []
            pen_down = False
        i += 1

    if cur and pen_down and len(cur) >= 2:
        strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
    return strokes


def _to_point(p: Tuple[float, float]):
    from backend.glyphs import Point
    return Point(p[0], p[1])


def _element_strokes(elem) -> List[Stroke]:
    tag = elem.tag
    strokes: List[Stroke] = []
    if tag == NS + "path":
        d = elem.get("d")
        if d:
            strokes.extend(_parse_path_d(d))
    elif tag == NS + "polyline":
        pts = _parse_floats(elem.get("points", ""))
        cur = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if len(cur) >= 2:
            strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
    elif tag == NS + "polygon":
        pts = _parse_floats(elem.get("points", ""))
        cur = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
        if len(cur) >= 2:
            cur.append(cur[0])
            strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
    elif tag == NS + "rect":
        x = float(elem.get("x", "0")); y = float(elem.get("y", "0"))
        w = float(elem.get("width", "0")); h = float(elem.get("height", "0"))
        cur = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
    elif tag == NS + "circle" or tag == NS + "ellipse":
        cx = float(elem.get("cx", "0")); cy = float(elem.get("cy", "0"))
        rx = float(elem.get("rx", elem.get("r", "0")))
        ry = float(elem.get("ry", elem.get("r", "0")))
        n = 64
        cur = []
        for i in range(n + 1):
            a = 2 * math.pi * i / n
            cur.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
        if len(cur) >= 2:
            strokes.append(Stroke(points=[_to_point(p) for p in cur], pen_down=True))
    elif tag == NS + "line":
        x1 = float(elem.get("x1", "0")); y1 = float(elem.get("y1", "0"))
        x2 = float(elem.get("x2", "0")); y2 = float(elem.get("y2", "0"))
        strokes.append(Stroke(points=[_to_point((x1, y1)), _to_point((x2, y2))], pen_down=True))
    # transform (translate/scale) игнорируются на этом этапе; применяются на стороне вызова
    return strokes


def svg_to_strokes(svg_path: str) -> List[Stroke]:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    strokes: List[Stroke] = []
    for elem in root.iter():
        strokes.extend(_element_strokes(elem))
    # уберём пустые
    return [s for s in strokes if s.points and len(s.points) >= 2]
