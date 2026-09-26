from __future__ import annotations

import os

from app.services.email.base import EmailProvider

PROVIDER_REGISTRY = {
    "mailjet": "app.services.email.mailjet:MailjetProvider",
    "sendgrid": "app.services.email.sendgrid:SendGridProvider",
}

_active: EmailProvider | None = None


def _load(path: str) -> EmailProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def get_email_provider() -> EmailProvider:
    global _active
    if _active is not None:
        return _active
    name = os.environ.get("EMAIL_PROVIDER", "mailjet").strip().lower() or "mailjet"
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported EMAIL_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
