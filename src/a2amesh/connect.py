"""连接辅助：从 Config 加载 .env 并连接 NATS。"""
from __future__ import annotations

import os

import nats
from dotenv import load_dotenv

from a2amesh.config import Config


async def connect(cfg: Config):
    load_dotenv()
    seed = os.environ.get(cfg.nats.nkey_seed_env)
    kwargs = {"nkeys_seed": seed} if seed else {}
    return await nats.connect(cfg.nats.url, **kwargs)
