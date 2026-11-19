"""Конфигурация программного комплекса управления роботом-плоттером.

Все аппаратные параметры (команды сервопривода/оси Z, скорости подачи F,
масштаб шагов, рабочая область) вынесены сюда явно с комментариями.
Загружается из config.yaml; если файла нет — используются значения по умолчанию.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml опционален, есть встроенные default-ы
    yaml = None  # type: ignore


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
PRESETS_DIR = os.path.join(BASE_DIR, "presets")
GCODE_DIR = os.path.join(BASE_DIR, "gcode")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")


@dataclass
class SerialConfig:
    """Параметры USB-Serial соединения с GRBL."""
    port: str = ""                     # например /dev/ttyUSB0 или /dev/ttyACM0 (пусто = симуляция)
    baudrate: int = 115200             # GRBL по умолчанию 115200 (старые версии — 9600/57600)
    timeout: float = 2.0               # таймаут чтения ответа от GRBL, сек
    write_timeout: float = 2.0         # таймаут записи в порт, сек
    max_inflight: int = 4              # сколько команд можно отослать без подтверждения 'ok'
                                       # GRBL имеет кольцевой буфер (~128 байт v1.1),
                                       # держим мало незавершённых команд, чтобы не переполнить
    simulate: bool = False             # если True — не открывает реальный COM-порт


@dataclass
class MachineConfig:
    """Аппаратные параметры плоттера и шагов."""
    steps_per_mm_x: float = 80.0       # шагов шагового двигателя на 1 мм по X
    steps_per_mm_y: float = 80.0       # шагов шагового двигателя на 1 мм по Y
    work_width_mm: float = 210.0       # ширина рабочей зоны (A4 ширина = 210 мм)
    work_height_mm: float = 300.0      # высота рабочей зоны (по ТЗ 21×30 см = 210×300 мм)
    origin_corner: str = "top-left"    # где ноль (0,0): top-left | bottom-left
    y_inverted: bool = True            # инвертировать ось Y в G-code (Y вниз по листу)
    homing_enabled: bool = True        # поддерживает ли GRBL хoming ($H)
    # Координата/Home смещение после сброса нуля (G92/G10), мм:
    zero_offset_x: float = 0.0
    zero_offset_y: float = 0.0


@dataclass
class PenConfig:
    """Параметры подъёма/опускания ручки (ось Z / серво / шпиндель PWM)."""
    mode: str = "z"                    # z | servo | spindle
                                         # z        — ось Z (G1 Z<pos>), дефолт для רוב plotter'ов
                                         # servo      — серво через M3 S<val>/M5 или M280 (если прошивка поддерживает)
                                         # spindle    — шпиндель GRBL (M3/M5)
    z_down_mm: float = -1.5            # положение пера «пишет» (касается бумаги), мм
    z_up_mm: float = 3.0               # положение пера «поднято» над бумагой, мм
    z_feed: float = 600.0              # скорость подъёма/опускания перо, мм/мин
    # Альтернативные команды для режимов servo/spindle (формируются f-строкой):
    pen_down_cmd: str = "G1 Z{z_down} F{z_feed}"
    pen_up_cmd: str = "G1 Z{z_up} F{z_feed}"


@dataclass
class FeedConfig:
    """Скорости подачи (F) в мм/мин."""
    write: float = 800.0               # скорость написания/черчения (перо опущено)
    travel: float = 2500.0             # скорость перемещения перо поднято (переход между штрихами)
    jog: float = 1500.0                # скорость ручного перемещения осей X/Y
    z: float = 600.0                   # скорость оси Z (подъём/опускание перо)


@dataclass
class AppConfig:
    """Общая конфигурация приложения."""
    serial: SerialConfig = field(default_factory=SerialConfig)
    machine: MachineConfig = field(default_factory=MachineConfig)
    pen: PenConfig = field(default_factory=PenConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    # Веб-сервер
    host: str = "0.0.0.0"
    port: int = 8000
    # Размер шрифта по умолчанию, мм (высота прописной буквы approx)
    default_font_size_mm: float = 8.0
    default_line_height_mm: float = 14.0
    default_tracking_mm: float = 1.0   # интервал между буквами (追踪)
    default_word_spacing_mm: float = 2.5
    default_slant_deg: float = 0.0     # наклон шрифта (вариативный курсив), градусы


def load_config(path: str = CONFIG_PATH) -> AppConfig:
    """Загрузить конфиг из YAML, дополнив значениями по умолчанию."""
    cfg = AppConfig()
    if not os.path.exists(path):
        return cfg
    if yaml is None:
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # Аккуратное слияние секций
    for section, cls in (
        ("serial", SerialConfig),
        ("machine", MachineConfig),
        ("pen", PenConfig),
        ("feed", FeedConfig),
    ):
        if section in data and isinstance(data[section], dict):
            for k, v in data[section].items():
                if hasattr(getattr(cfg, section), k):
                    setattr(getattr(cfg, section), k, v)
    for k in ("host", "port", "default_font_size_mm", "default_line_height_mm",
              "default_tracking_mm", "default_word_spacing_mm", "default_slant_deg"):
        if k in data:
            setattr(cfg, k, data[k])
    return cfg


def save_config(cfg: AppConfig, path: str = CONFIG_PATH) -> None:
    """Сохранить конфиг в YAML (опционально)."""
    if yaml is None:
        return
    data = {
        "serial": {k: getattr(cfg.serial, k) for k in vars(cfg.serial)},
        "machine": {k: getattr(cfg.machine, k) for k in vars(cfg.machine)},
        "pen": {k: getattr(cfg.pen, k) for k in vars(cfg.pen)},
        "feed": {k: getattr(cfg.feed, k) for k in vars(cfg.feed)},
        "host": cfg.host,
        "port": cfg.port,
        "default_font_size_mm": cfg.default_font_size_mm,
        "default_line_height_mm": cfg.default_line_height_mm,
        "default_tracking_mm": cfg.default_tracking_mm,
        "default_word_spacing_mm": cfg.default_word_spacing_mm,
        "default_slant_deg": cfg.default_slant_deg,
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


CONFIG: AppConfig = load_config()
