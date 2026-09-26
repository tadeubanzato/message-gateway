from __future__ import annotations

from app.services.env import get_env

from app.services.push.base import PushProvider

# Pushover stays the default (not ntfy) — deliberate decision, don't flip.
PROVIDER_REGISTRY = {
    "pushover": "app.services.push.pushover:PushoverProvider",
    "ntfy": "app.services.push.ntfy:NtfyProvider",
}

_cache: dict[str, PushProvider] = {}


def _load(path: str) -> PushProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def default_provider_name() -> str:
    """The channel's default provider (what a message uses when it names none)."""
    return (get_env("PUSH_PROVIDER", "pushover") or "pushover").strip().lower() or "pushover"


def get_push_provider(name: str | None = None) -> PushProvider:
    """Provider instance by name (default: the channel default). Resolved on every
    call, so changes made in Settings apply without a restart."""
    name = (name or default_provider_name()).strip().lower()
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported push provider {name!r}. Options: {list(PROVIDER_REGISTRY)}")
    if name not in _cache:
        _cache[name] = _load(PROVIDER_REGISTRY[name])
    return _cache[name]
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported PUSH_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    _active_name = name
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
