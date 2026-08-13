"""JSON 结构化日志。"""
from __future__ import annotations

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("agent", "task_id", "correlation_id"):
            value = getattr(record, key, None)
            if value:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """幂等初始化 a2amesh 根 logger（JSON 行输出到 stderr）。"""
    logger = logging.getLogger("a2amesh")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
