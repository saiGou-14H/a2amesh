"""连接辅助：从 Config 加载 .env 并连接 NATS。"""
from __future__ import annotations

import os

from dotenv import load_dotenv

import nats
from a2amesh.config import Config


async def connect(cfg: Config):
    load_dotenv()
    seed = os.environ.get(cfg.nats.nkey_seed_env)
    kwargs = {
        "inbox_prefix": f"_INBOX.{cfg.agent.name}",
    }
    if seed:
        kwargs["nkeys_seed_str"] = seed
    return await nats.connect(cfg.nats.url, **kwargs)
