from __future__ import annotations

from app.services.env import get_env

from app.services.telegram.base import TelegramProvider

PROVIDER_REGISTRY = {
    "bot_api": "app.services.telegram.bot_api:TelegramBotProvider",
}

_cache: dict[str, TelegramProvider] = {}


def _load(path: str) -> TelegramProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def default_provider_name() -> str:
    """The channel's default provider. Must match channels.DEFAULT_PROVIDER["telegram"]."""
    return (get_env("TELEGRAM_PROVIDER", "bot_api") or "bot_api").strip().lower() or "bot_api"


def get_telegram_provider(name: str | None = None) -> TelegramProvider:
    """Provider instance by name (default: the channel default). Resolved on every
    call, so changes made in Settings apply without a restart."""
    name = (name or default_provider_name()).strip().lower()
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported telegram provider {name!r}. Options: {list(PROVIDER_REGISTRY)}")
    if name not in _cache:
        _cache[name] = _load(PROVIDER_REGISTRY[name])
    return _cache[name]


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
