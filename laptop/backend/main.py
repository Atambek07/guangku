"""FastAPI бэкенд: REST API + WebSocket для управления плоттером через GRBL.

Запуск:  uvicorn main:app --host 0.0.0.0 --port 8000
Из корня проекта:  uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# Позволяет запускать как `uvicorn backend.main:app` из корня,
# так и `python3 backend/main.py` напрямую.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import CONFIG, CONFIG_PATH, FONTS_DIR, PRESETS_DIR, GCODE_DIR, OUTPUT_DIR, LOGS_DIR, save_config
from backend.fonts import scan_fonts, find_font, default_font
from backend.glyphs import GlyphEngine, text_to_strokes
from backend.layout import Preset, Page, LayoutEngine, load_presets
from backend.gcodegen import GCodeGenerator, svg_to_strokes
from backend.serial_link import GrblLink, MachineState
from backend.jobs import JobQueue, JobStatus
from backend.preview import render_pages_png


app = FastAPI(title="Графдзеркало — плоттер-конспект", version="1.0.0")

# ---------- глобальное состояние ----------
grbl = GrblLink()
job_queue = JobQueue()

# статус -> последняя отметка
latest_status = {"state": MachineState.DISCONNECTED, "progress": 0.0, "message": ""}


def _status_cb(status):
    latest_status["state"] = status.state
    latest_status["progress"] = status.progress
    latest_status["message"] = status.message


grbl.on_status = _status_cb

# ---------- вспомогательная сериализация ----------
def _stroke_to_dict(stroke) -> Dict[str, Any]:
    return {
        "points": [{"x": round(p.x, 3), "y": round(p.y, 3)} for p in stroke.points],
        "pen_down": bool(stroke.pen_down),
    }


def _page_to_dict(page: Page) -> Dict[str, Any]:
    return {
        "page_no": page.page_no,
        "width_mm": page.width_mm,
        "height_mm": page.height_mm,
        "strokes": [_stroke_to_dict(s) for s in page.strokes],
        "char_count": page.char_count,
    }


def _dict_to_stroke(d: Dict[str, Any]):
    from backend.glyphs import Stroke, Point
    pts = [Point(p["x"], p["y"]) for p in d.get("points", [])]
    return Stroke(points=pts, pen_down=bool(d.get("pen_down", True)))


# ---------- модели запросов ----------
class LayoutRequest(BaseModel):
    text: str
    preset_id: str = "notebook_grid_5mm"
    font_name: Optional[str] = None
    font_size_mm: Optional[float] = None
    line_height_mm: Optional[float] = None
    tracking_mm: Optional[float] = None
    word_spacing_mm: Optional[float] = None
    slant_deg: Optional[float] = None
    cell_size_mm: Optional[float] = None
    line_spacing_mm: Optional[float] = None
    margin_left_mm: Optional[float] = None
    margin_right_mm: Optional[float] = None
    margin_top_mm: Optional[float] = None
    margin_bottom_mm: Optional[float] = None
    red_line_indent_mm: Optional[float] = None


class GCodeRequest(BaseModel):
    pages: List[Dict[str, Any]]
    filename: Optional[str] = None
    job_name: Optional[str] = "plot"


class SerialConnect(BaseModel):
    port: Optional[str] = None
    baudrate: Optional[int] = None


class JogRequest(BaseModel):
    axis: str = "X"
    mm: float = 10.0
    feed: Optional[float] = None


# ---------- шрифты и пресеты ----------
@app.get("/api/fonts")
def api_fonts():
    infos = scan_fonts(FONTS_DIR)
    return {
        "fonts": [
            {
                "name": i.name,
                "filename": i.filename,
                "units_per_em": i.units_per_em,
                "cyrillic_present": i.cyrillic_present,
                "cyrillic_total": i.cyrillic_total,
                "cyrillic_coverage": i.cyrillic_coverage,
                "sample_ok": i.sample_ok,
                "missing_sample": i.missing_sample,
                "error": i.error,
            }
            for i in infos
        ],
        "default": default_font(FONTS_DIR).name if default_font(FONTS_DIR) else None,
    }


@app.get("/api/presets")
def api_presets():
    presets = load_presets(PRESETS_DIR)
    return {
        "presets": [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "work_width_mm": p.work_width_mm,
                "work_height_mm": p.work_height_mm,
                "margin_left_mm": p.margin_left_mm,
                "margin_right_mm": p.margin_right_mm,
                "margin_top_mm": p.margin_top_mm,
                "margin_bottom_mm": p.margin_bottom_mm,
                "cell_size_mm": p.cell_size_mm,
                "line_spacing_mm": p.line_spacing_mm,
                "font_size_mm": p.font_size_mm,
                "line_height_mm": p.line_height_mm,
                "tracking_mm": p.tracking_mm,
                "slant_deg": p.slant_deg,
                "red_line_indent_mm": p.red_line_indent_mm,
                "font_name": p.font_name,
            }
            for p in presets
        ]
    }


# ---------- разметка ----------
@app.post("/api/layout")
def api_layout(req: LayoutRequest):
    presets = {p.id: p for p in load_presets(PRESETS_DIR)}
    if req.preset_id not in presets:
        raise HTTPException(status_code=404, detail=f"Пресет {req.preset_id} не найден")
    preset = presets[req.preset_id]

    # наложим переопределения из запроса
    for field_name in ("font_size_mm", "line_height_mm", "tracking_mm", "word_spacing_mm",
                       "slant_deg", "cell_size_mm", "line_spacing_mm", "margin_left_mm",
                       "margin_right_mm", "margin_top_mm", "margin_bottom_mm",
                       "red_line_indent_mm", "font_name"):
        val = getattr(req, field_name)
        if val is not None:
            setattr(preset, field_name, val)

    font_path = find_font(FONTS_DIR, preset.font_name or req.font_name or "")
    if not font_path:
        df = default_font(FONTS_DIR)
        font_path = df.path if df else ""
        preset.font_name = (df.name if df else "")

    engine = LayoutEngine(preset, font_path)
    result: LayoutResult = engine.layout(req.text)

    return {
        "preset": {
            "id": preset.id, "name": preset.name, "kind": preset.kind,
            "work_width_mm": preset.work_width_mm, "work_height_mm": preset.work_height_mm,
            "font_name": preset.font_name, "font_path": font_path,
        },
        "page_count": result.page_count,
        "total_chars": result.total_chars,
        "total_strokes": result.total_strokes,
        "est_time_min": result.est_time_min,
        "preview_svg": result.preview_svg,
        "pages": [_page_to_dict(pg) for pg in result.pages],
    }


# ---------- G-code ----------
@app.post("/api/gcode")
def api_gcode(req: GCodeRequest):
    pages = []
    for pd in req.pages:
        page = Page(page_no=pd.get("page_no", 1),
                    width_mm=pd.get("width_mm", CONFIG.machine.work_width_mm),
                    height_mm=pd.get("height_mm", CONFIG.machine.work_height_mm))
        for sd in pd.get("strokes", []):
            page.strokes.append(_dict_to_stroke(sd))
        pages.append(page)

    gen = GCodeGenerator()
    filename = req.filename or f"job_{os.getpid()}.nc"
    res: GCodeResult = gen.generate(pages, filename=filename, job_name=req.job_name or "plot")
    return {
        "ok": True,
        "filename": res.filename,
        "path": res.path,
        "line_count": res.line_count,
        "est_time_min": res.est_time_min,
        "mm_total": res.mm_total,
        "gcode": res.gcode,
    }


# ---------- SVG импорт ----------
@app.post("/api/svg")
async def api_svg(file: UploadFile = File(...)):
    contents = await file.read()
    tmp = os.path.join(OUTPUT_DIR, f"upload_{file.filename}")
    with open(tmp, "wb") as fh:
        fh.write(contents)
    strokes = svg_to_strokes(tmp)
    return {
        "ok": True,
        "strokes": [_stroke_to_dict(s) for s in strokes],
        "count": len(strokes),
        "saved": tmp,
    }


# ---------- Serial / GRBL ----------
@app.get("/api/serial/ports")
def api_serial_ports():
    return {"ports": GrblLink.list_ports()}


@app.post("/api/serial/connect")
def api_serial_connect(req: SerialConnect):
    res = grbl.connect(port=req.port, baudrate=req.baudrate)
    return res


@app.post("/api/serial/disconnect")
def api_serial_disconnect():
    return grbl.disconnect()


@app.get("/api/serial/status")
def api_serial_status():
    st = grbl.status()
    return {
        "state": st.state,
        "mpos": st.mpos,
        "wpos": st.wpos,
        "buffer_ok": st.buffer_ok,
        "line": st.line,
        "total": st.total,
        "progress": st.progress,
        "message": st.message,
    }


@app.post("/api/serial/stream")
def api_serial_stream(gcode: str):
    return grbl.stream_gcode(gcode)


@app.post("/api/serial/pause")
def api_serial_pause():
    return grbl.pause()


@app.post("/api/serial/resume")
def api_serial_resume():
    return grbl.resume()


@app.post("/api/serial/stop")
def api_serial_stop():
    return grbl.stop()


@app.post("/api/serial/home")
def api_serial_home():
    return grbl.home()


@app.post("/api/serial/zero")
def api_serial_zero():
    return grbl.zero()


@app.post("/api/serial/jog")
def api_serial_jog(req: JogRequest):
    return grbl.jog(req.axis, req.mm, req.feed)


@app.post("/api/serial/pen_down")
def api_serial_pen_down():
    return grbl.pen_down()


@app.post("/api/serial/pen_up")
def api_serial_pen_up():
    return grbl.pen_up()


# ---------- WebSocket ----------
@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    # отдаём текущий статус сразу
    await websocket.send_text(json.dumps({
        "type": "status",
        "state": latest_status["state"],
        "progress": latest_status["progress"],
        "message": latest_status["message"],
    }))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await websocket.send_text(json.dumps({"type": "error", "message": "Некорректный JSON"}))
                continue
            mtype = msg.get("type")
            if mtype == "get_status":
                st = grbl.status()
                await websocket.send_text(json.dumps({
                    "type": "status",
                    "state": st.state,
                    "progress": st.progress,
                    "message": st.message,
                    "line": st.line,
                    "total": st.total,
                }))
            elif mtype == "start":
                gcode = msg.get("gcode", "")
                res = grbl.stream_gcode(gcode)
                await websocket.send_text(json.dumps({"type": "start", "ok": res.get("ok"), "error": res.get("error"), "queued": res.get("queued")}))
            elif mtype == "pause":
                res = grbl.pause()
                await websocket.send_text(json.dumps({"type": "pause", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "resume":
                res = grbl.resume()
                await websocket.send_text(json.dumps({"type": "resume", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "stop":
                res = grbl.stop()
                await websocket.send_text(json.dumps({"type": "stop", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "home":
                res = grbl.home()
                await websocket.send_text(json.dumps({"type": "home", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "zero":
                res = grbl.zero()
                await websocket.send_text(json.dumps({"type": "zero", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "jog":
                res = grbl.jog(msg.get("axis", "X"), float(msg.get("mm", 10)), msg.get("feed"))
                await websocket.send_text(json.dumps({"type": "jog", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "pen_down":
                res = grbl.pen_down()
                await websocket.send_text(json.dumps({"type": "pen_down", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "pen_up":
                res = grbl.pen_up()
                await websocket.send_text(json.dumps({"type": "pen_up", "ok": res.get("ok"), "error": res.get("error")}))
            elif mtype == "connect":
                res = grbl.connect(port=msg.get("port"), baudrate=msg.get("baudrate"))
                await websocket.send_text(json.dumps({"type": "connect", **res}))
            elif mtype == "disconnect":
                res = grbl.disconnect()
                await websocket.send_text(json.dumps({"type": "disconnect", "ok": res.get("ok")}))
            else:
                await websocket.send_text(json.dumps({"type": "error", "message": f"Неизвестная команда {mtype}"}))
    except WebSocketDisconnect:
        pass


# ---------- статика и главная страница ----------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/config.yaml", response_class=PlainTextResponse)
def api_config_yaml():
    """Отдаёт текущий config.yaml (для просмотра в интерфейсе настроек)."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    return "# config.yaml не найден — используются значения по умолчанию"


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = os.path.join("static", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as fh:
            return fh.read()
    return HTMLResponse("<h3>Бэкенд работает. Файл static/index.html пока не создан.</h3>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port)
