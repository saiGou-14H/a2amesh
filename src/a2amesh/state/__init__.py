"""State-plane building blocks, exported lazily to keep config imports light."""

from importlib import import_module

_EXPORT_MODULES = {
    "KeyBuilderError": ".key_builder",
    "KeyKind": ".key_builder",
    "KeyPart": ".key_builder",
    "KeyPartCodec": ".key_builder",
    "RedisKeyBuilder": ".key_builder",
    "RedisConfig": ".config",
    "RedisConfigError": ".config",
    "RedisClient": ".client",
    "RedisClientError": ".client",
    "RedisNoScriptError": ".client",
    "AuthReplayClaimError": ".script_runner",
    "AuthReplayClaimRequest": ".script_runner",
    "AuthReplayClaimResult": ".script_runner",
    "AuthReplayClaimRunner": ".script_runner",
    "auth_replay_script_sha1": ".script_runner",
    "auth_replay_script_source": ".script_runner",
}

__all__ = tuple(_EXPORT_MODULES)


def __getattr__(name: str) -> object:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
