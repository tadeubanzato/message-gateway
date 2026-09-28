from __future__ import annotations

from app.services.env import get_env

from app.services.whatsapp.base import WhatsAppProvider

PROVIDER_REGISTRY = {
    "cloud_api": "app.services.whatsapp.cloud_api:WhatsAppCloudProvider",
}

_cache: dict[str, WhatsAppProvider] = {}


def _load(path: str) -> WhatsAppProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def default_provider_name() -> str:
    """The channel's default provider. Must match channels.DEFAULT_PROVIDER["whatsapp"]."""
    return (get_env("WHATSAPP_PROVIDER", "cloud_api") or "cloud_api").strip().lower() or "cloud_api"


def get_whatsapp_provider(name: str | None = None) -> WhatsAppProvider:
    """Provider instance by name (default: the channel default). Resolved on every
    call, so changes made in Settings apply without a restart."""
    name = (name or default_provider_name()).strip().lower()
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported whatsapp provider {name!r}. Options: {list(PROVIDER_REGISTRY)}")
    if name not in _cache:
        _cache[name] = _load(PROVIDER_REGISTRY[name])
    return _cache[name]


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
