"""Tracker：每步尝试次数与耗时记录。"""
from __future__ import annotations

import time


class Tracker:
    def __init__(self):
        self.attempts: dict[str, int] = {}
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}

    def start(self, step_id: str, attempt: int):
        self.attempts[step_id] = attempt
        self.started[step_id] = time.time()

    def finish(self, step_id: str):
        self.finished[step_id] = time.time()

    def duration(self, step_id: str) -> float:
        return self.finished.get(step_id, time.time()) - self.started.get(step_id, time.time())
