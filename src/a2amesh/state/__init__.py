"""State-plane building blocks, exported lazily to keep config imports light."""

from importlib import import_module

_EXPORT_MODULES = {
    "RedisConfig": ".config",
    "RedisConfigError": ".config",
    "RedisClient": ".client",
    "RedisClientError": ".client",
}

__all__ = tuple(_EXPORT_MODULES)


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
