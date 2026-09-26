from __future__ import annotations

import os

from app.services.sms.base import SmsProvider

PROVIDER_REGISTRY = {
    "twilio": "app.services.sms.twilio:TwilioProvider",
    "infobip": "app.services.sms.infobip:InfobipProvider",
    "local_modem": "app.services.sms.local_modem:LocalModemProvider",
}

_active: SmsProvider | None = None


def _load(path: str) -> SmsProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def get_sms_provider() -> SmsProvider:
    global _active
    if _active is not None:
        return _active
    name = os.environ.get("SMS_PROVIDER", "twilio").strip().lower() or "twilio"
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported SMS_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
