from __future__ import annotations

import os

from app.services.push.base import PushProvider

# Pushover stays the default (not ntfy) — deliberate decision, don't flip.
PROVIDER_REGISTRY = {
    "pushover": "app.services.push.pushover:PushoverProvider",
    "ntfy": "app.services.push.ntfy:NtfyProvider",
}

_active: PushProvider | None = None


def _load(path: str) -> PushProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def get_push_provider() -> PushProvider:
    global _active
    if _active is not None:
        return _active
    name = os.environ.get("PUSH_PROVIDER", "pushover").strip().lower() or "pushover"
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported PUSH_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
