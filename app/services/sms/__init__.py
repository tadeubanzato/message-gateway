from __future__ import annotations

from app.services.env import get_env

from app.services.sms.base import SmsProvider

PROVIDER_REGISTRY = {
    "custom_http": "app.services.sms.custom_http:CustomHttpProvider",
    "twilio": "app.services.sms.twilio:TwilioProvider",
    "infobip": "app.services.sms.infobip:InfobipProvider",
    "sinch": "app.services.sms.sinch:SinchProvider",
}

_cache: dict[str, SmsProvider] = {}


def _load(path: str) -> SmsProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def default_provider_name() -> str:
    """The channel's default provider (what a message uses when it names none).
    Must match channels.DEFAULT_PROVIDER["sms"] - that's what the web app shows
    as the default before anyone picks one."""
    return (get_env("SMS_PROVIDER", "custom_http") or "custom_http").strip().lower() or "custom_http"


def get_sms_provider(name: str | None = None) -> SmsProvider:
    """Provider instance by name (default: the channel default). Resolved on every
    call, so changes made in Settings apply without a restart."""
    name = (name or default_provider_name()).strip().lower()
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported sms provider {name!r}. Options: {list(PROVIDER_REGISTRY)}")
    if name not in _cache:
        _cache[name] = _load(PROVIDER_REGISTRY[name])
    return _cache[name]
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported SMS_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    _active_name = name
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
