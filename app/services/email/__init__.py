from __future__ import annotations

from app.services.env import get_env

from app.services.email.base import EmailProvider

PROVIDER_REGISTRY = {
    "mailjet": "app.services.email.mailjet:MailjetProvider",
    "sendgrid": "app.services.email.sendgrid:SendGridProvider",
}

_cache: dict[str, EmailProvider] = {}


def _load(path: str) -> EmailProvider:
    module_path, cls_name = path.split(":")
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)()


def default_provider_name() -> str:
    """The channel's default provider (what a message uses when it names none)."""
    return (get_env("EMAIL_PROVIDER", "mailjet") or "mailjet").strip().lower() or "mailjet"


def get_email_provider(name: str | None = None) -> EmailProvider:
    """Provider instance by name (default: the channel default). Resolved on every
    call, so changes made in Settings apply without a restart."""
    name = (name or default_provider_name()).strip().lower()
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported email provider {name!r}. Options: {list(PROVIDER_REGISTRY)}")
    if name not in _cache:
        _cache[name] = _load(PROVIDER_REGISTRY[name])
    return _cache[name]
    if name not in PROVIDER_REGISTRY:
        raise SystemExit(f"Unsupported EMAIL_PROVIDER={name!r}. Options: {list(PROVIDER_REGISTRY)}")
    _active = _load(PROVIDER_REGISTRY[name])
    _active_name = name
    return _active


def list_providers() -> list[str]:
    return list(PROVIDER_REGISTRY.keys())
