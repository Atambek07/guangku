"""Очередь задач печати и отслеживание прогресса."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class Job:
    id: str
    name: str
    gcode: str
    status: JobStatus = JobStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    line: int = 0
    total: int = 0
    progress: float = 0.0
    message: str = ""


class JobQueue:
    def __init__(self):
        self._jobs: dict = {}
        self._current: Optional[Job] = None
        self._lock = threading.Lock()

    def add(self, name: str, gcode: str) -> Job:
        job = Job(id=str(uuid.uuid4()), name=name, gcode=gcode)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self):
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def set_current(self, job: Optional[Job]) -> None:
        with self._lock:
            self._current = job

    def current(self) -> Optional[Job]:
        with self._lock:
            return self._current

    def update_progress(self, line: int, total: int, status: JobStatus, message: str = "") -> None:
        with self._lock:
            if self._current:
                self._current.line = line
                self._current.total = total
                self._current.progress = round(100.0 * line / total, 1) if total else 0.0
                self._current.status = status
                if message:
                    self._current.message = message
                if status in (JobStatus.DONE, JobStatus.STOPPED, JobStatus.ERROR):
                    self._current.finished_at = time.time()
                    if status == JobStatus.DONE and not self._current.started_at:
                        self._current.started_at = self._current.created_at
