"""Соединение с GRBL по USB-Serial и потоковая передача G-code.

Особенности GRBL:
  - кольцевой буфер incoming (~128 байт на GRBL v1.1). Если слать команды
    быстрее, чем станок их выполняет, буфер переполняется и порт забивается.
    Мы держим не более max_inflight неподтверждённых команд и ждём 'ok'.
  - поток управления в реальном времени: '!' = feed hold (пауза),
    '~' = resume (восстановление), 'X' = сброс/стоп (hard stop),
    '?' = статус-отчёт.
  - '$H' = homing, '$J=G91 X.. Y.. F..' = jog (ручное перемещение),
    'G92 X0 Y0' / 'G10 L20 P1 X0 Y0' = установка нуля.

Режим simulate=True работает без железа — полезен для разработки и тестов.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional

import serial
import serial.tools.list_ports

from backend.config import CONFIG


class MachineState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    IDLE = "idle"
    RUN = "run"
    HOLD = "hold"      # пауза (feed hold)
    ALARM = "alarm"
    STOP = "stop"      # жёсткий стоп / сброс
    HOMING = "homing"
    JOG = "jog"
    ERROR = "error"


@dataclass
class GrblStatus:
    state: MachineState = MachineState.DISCONNECTED
    mpos: str = ""
    wpos: str = ""
    buffer_ok: bool = True
    line: int = 0
    total: int = 0
    progress: float = 0.0
    message: str = ""


class GrblLink:
    def __init__(self, on_status: Optional[Callable[[GrblStatus], None]] = None):
        self.cfg = CONFIG.serial
        self.on_status = on_status
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.RLock()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_reader = False
        self._pump_thread: Optional[threading.Thread] = None
        self._stop_pump = False

        self._queue: Deque[str] = deque()          # команды на отправку
        self._inflight: int = 0                     # отправлено, не подтверждено
        self._acked = threading.Event()             # пришло 'ok'
        self._status = GrblStatus()
        self._sent_count = 0
        self._total_count = 0
        self._paused_by_user = False

    # ---------- подключение / порты ----------
    @staticmethod
    def list_ports() -> List[dict]:
        out: List[dict] = []
        for p in serial.tools.list_ports.comports():
            out.append({
                "device": p.device,
                "description": p.description or "",
                "hwid": p.hwid or "",
            })
        return out

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> dict:
        with self._lock:
            if self._ser and self._ser.is_open:
                return {"ok": True, "port": self._ser.port, "simulate": self.cfg.simulate}
            port = port or self.cfg.port or ""
            baudrate = baudrate or self.cfg.baudrate
            self.cfg.simulate = not port or port.lower() in ("sim", "none", "off")
            if self.cfg.simulate:
                self._status.state = MachineState.CONNECTED
                self._status.message = "Симуляционный режим (без железа)"
                self._broadcast()
                return {"ok": True, "port": "sim", "simulate": True, "message": self._status.message}
            try:
                self._ser = serial.Serial(port, baudrate,
                                          timeout=self.cfg.timeout,
                                          write_timeout=self.cfg.write_timeout)
                time.sleep(2.0)  # GRBL перезагружается при открытии порта
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()
                self._stop_reader = False
                self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
                self._reader_thread.start()
                self._status.state = MachineState.CONNECTED
                self._status.message = f"Подключено к {port} @ {baudrate}"
                self._broadcast()
                return {"ok": True, "port": port, "simulate": False, "message": self._status.message}
            except serial.SerialException as exc:
                self._status.state = MachineState.DISCONNECTED
                self._status.message = f"Ошибка порта: {exc}"
                self._broadcast()
                return {"ok": False, "error": str(exc)}

    def disconnect(self) -> dict:
        with self._lock:
            self.stop_job()
            self._stop_reader = True
            self._stop_pump = True
            if self._ser and self._ser.is_open:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None
            self._queue.clear()
            self._inflight = 0
            self._status.state = MachineState.DISCONNECTED
            self._status.message = "Отключено"
            self._broadcast()
            return {"ok": True}

    # ---------- потоки ----------
    def _reader_loop(self) -> None:
        while not self._stop_reader and self._ser:
            try:
                line = self._ser.readline()
            except Exception:
                break
            if not line:
                continue
            try:
                text = line.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            if not text:
                continue
            self._handle_grbl_line(text)

    def _handle_grbl_line(self, text: str) -> None:
        t = text.strip()
        with self._lock:
            if t == "ok":
                self._inflight = max(0, self._inflight - 1)
                self._acked.set()
                self._status.buffer_ok = True
                self._status.state = MachineState.IDLE
                self._status.message = "ok"
                self._broadcast()
                return
            if t.startswith("error"):
                self._status.state = MachineState.ERROR
                self._status.message = t
                self._broadcast()
                return
            if t.startswith("alarm") or t.startswith("ALARM"):
                self._status.state = MachineState.ALARM
                self._status.message = t
                self._broadcast()
                return
            if t.startswith("<") and t.endswith(">"):
                # GRBL статус: <Idle|MPos:...|WPos:...|...|...>
                self._parse_status_report(t)
                return
            if t.startswith("["):
                # сообщение printer/GRBL
                self._status.message = t
                self._broadcast()
                return

    def _parse_status_report(self, report: str) -> None:
        # <Idle|MPos:0.000,0.000,0.000|WPos:0.000,0.000,0.000|...>
        body = report[1:-1]
        parts = body.split("|")
        state_map = {
            "Idle": MachineState.IDLE,
            "Run": MachineState.RUN,
            "Hold": MachineState.HOLD,
            "Alarm": MachineState.ALARM,
            "Door": MachineState.STOP,
            "Home": MachineState.HOMING,
            "Jog": MachineState.JOG,
        }
        state = state_map.get(parts[0], MachineState.IDLE)
        mpos = wpos = ""
        for part in parts[1:]:
            if part.startswith("MPos:"):
                mpos = part[5:]
            elif part.startswith("WPos:"):
                wpos = part[5:]
        self._status.state = state
        self._status.mpos = mpos
        self._status.wpos = wpos
        if self._total_count > 0:
            self._status.progress = round(100.0 * self._sent_count / self._total_count, 1)
        self._broadcast()

    def _pump_loop(self) -> None:
        """Отправляет команды из очереди, соблюдая лимит незавершённых."""
        while not self._stop_pump:
            self._acked.wait(timeout=0.05)
            with self._lock:
                if self._paused_by_user or self._status.state in (MachineState.ALARM, MachineState.ERROR):
                    continue
                while self._queue and self._inflight < self.cfg.max_inflight:
                    cmd = self._queue.popleft()
                    try:
                        if self.cfg.simulate:
                            self._inflight += 1
                            self._sent_count += 1
                            self._acked.clear()
                            # имитация выполнения
                            threading.Timer(0.05, self._sim_ack).start()
                        else:
                            self._ser.write((cmd + "\n").encode("utf-8"))
                            self._ser.flush()
                            self._inflight += 1
                            self._sent_count += 1
                            self._acked.clear()
                    except serial.SerialException as exc:
                        self._status.state = MachineState.ERROR
                        self._status.message = f"Ошибка записи: {exc}"
                        self._broadcast()
                        self._queue.clear()
                        break
            self._update_progress()

    def _sim_ack(self) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._acked.set()
            self._status.state = MachineState.IDLE if not self._queue else MachineState.RUN
            self._update_progress()
            self._broadcast()

    def _update_progress(self) -> None:
        if self._total_count > 0:
            self._status.progress = round(100.0 * self._sent_count / self._total_count, 1)
            self._status.line = self._sent_count
            self._status.total = self._total_count

    def _broadcast(self) -> None:
        if self.on_status:
            try:
                self.on_status(self._status)
            except Exception:
                pass

    # ---------- управление ----------
    def status(self) -> GrblStatus:
        with self._lock:
            return self._status

    def stream_gcode(self, gcode: str) -> dict:
        """Поставить G-code на потоковую отправку (с контролем буфера)."""
        with self._lock:
            if not self.cfg.simulate and (not self._ser or not self._ser.is_open):
                return {"ok": False, "error": "Нет соединения со станком"}
            lines = [l.strip() for l in gcode.splitlines() if l.strip() and not l.startswith(";")]
            # уберём заголовок/подвал не обязательно — отправляем как есть, кроме пустых/комментариев
            self._queue.extend(lines)
            self._total_count = len(lines)
            self._sent_count = 0
            self._paused_by_user = False
            self._status.state = MachineState.RUN if lines else MachineState.IDLE
            self._status.progress = 0.0
            self._status.message = f"Отправляется {len(lines)} команд"
            if not self._pump_thread or not self._pump_thread.is_alive():
                self._stop_pump = False
                self._pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
                self._pump_thread.start()
            self._acked.set()  # разбудить pump
            self._broadcast()
            return {"ok": True, "queued": len(lines)}

    def pause(self) -> dict:
        with self._lock:
            self._paused_by_user = True
            if self.cfg.simulate:
                self._status.state = MachineState.HOLD
                self._status.message = "Пауза (симуляция)"
                self._broadcast()
                return {"ok": True}
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(b"!\n")
                    self._ser.flush()
                except serial.SerialException as exc:
                    return {"ok": False, "error": str(exc)}
            self._status.state = MachineState.HOLD
            self._status.message = "Пауза (feed hold)"
            self._broadcast()
            return {"ok": True}

    def resume(self) -> dict:
        with self._lock:
            self._paused_by_user = False
            if self.cfg.simulate:
                self._status.state = MachineState.RUN if self._queue else MachineState.IDLE
                self._acked.set()
                self._broadcast()
                return {"ok": True}
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(b"~\n")
                    self._ser.flush()
                except serial.SerialException as exc:
                    return {"ok": False, "error": str(exc)}
            self._status.state = MachineState.RUN if self._queue else MachineState.IDLE
            self._status.message = "Возобновлено"
            self._broadcast()
            return {"ok": True}

    def stop(self) -> dict:
        """Жёсткая остановка (сброс GRBL)."""
        with self._lock:
            self._paused_by_user = False
            self._queue.clear()
            self._inflight = 0
            self._acked.set()
            if self.cfg.simulate:
                self._status.state = MachineState.IDLE
                self._status.message = "Стоп (симуляция)"
                self._broadcast()
                return {"ok": True}
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(b"X\n")
                    self._ser.flush()
                    time.sleep(0.1)
                    self._ser.reset_input_buffer()
                    self._ser.reset_output_buffer()
                except serial.SerialException as exc:
                    return {"ok": False, "error": str(exc)}
            self._status.state = MachineState.IDLE
            self._status.progress = 0.0
            self._status.message = "Стоп / сброс"
            self._broadcast()
            return {"ok": True}

    def home(self) -> dict:
        return self._send_raw("$H")

    def zero(self) -> dict:
        """Установить текущую позицию как ноль (G92)."""
        return self._send_raw("G92 X0 Y0")

    def jog(self, axis: str, mm: float, feed: Optional[float] = None) -> dict:
        """Ручное перемещение: $J=G91 X10 F1500 (относительное)."""
        f = feed or CONFIG.feed.jog
        cmd = f"$J=G91 {axis}{mm:.3f} F{f:.0f}"
        return self._send_raw(cmd)

    def pen_down(self) -> dict:
        return self._send_raw(CONFIG.pen.pen_down_cmd.format(
            z_down=CONFIG.pen.z_down_mm, z_up=CONFIG.pen.z_up_mm, z_feed=CONFIG.pen.z_feed))

    def pen_up(self) -> dict:
        return self._send_raw(CONFIG.pen.pen_up_cmd.format(
            z_down=CONFIG.pen.z_down_mm, z_up=CONFIG.pen.z_up_mm, z_feed=CONFIG.pen.z_feed))

    def _send_raw(self, cmd: str) -> dict:
        with self._lock:
            if self.cfg.simulate:
                self._status.message = f"Симуляция: {cmd}"
                self._broadcast()
                return {"ok": True}
            if not self._ser or not self._ser.is_open:
                return {"ok": False, "error": "Нет соединения"}
            try:
                self._ser.write((cmd + "\n").encode("utf-8"))
                self._ser.flush()
                self._status.message = f"Отправлено: {cmd}"
                self._broadcast()
                return {"ok": True}
            except serial.SerialException as exc:
                return {"ok": False, "error": str(exc)}
