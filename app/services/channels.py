"""
Channel/provider configuration shared by the first-run setup and the Settings page.

Holds the catalog of channels (email, SMS, push), the providers each supports and
the fields they need; and the operations on them: save credentials (encrypted, via
secret_store), verify them with the provider, send a test, and report status.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app.services import secret_store
from app.services.email import get_email_provider
from app.services.env import get_env
from app.services.push import get_push_provider
from app.services.sms import get_sms_provider

DEFAULT_PROVIDER = {"email": "mailjet", "sms": "twilio", "push": "pushover"}
CHANNEL_LABELS = {"email": "Email", "sms": "SMS", "push": "Push notifications"}

# Provider catalog: what the UI asks for. `secret` fields are masked in the UI.
# `extra` values are stored alongside (fixed wiring the user shouldn't have to know).
CATALOG: dict[str, dict[str, Any]] = {
    "email": {
        "selector": "EMAIL_PROVIDER",
        "getter": get_email_provider,
        "providers": {
            "mailjet": {
                "label": "Mailjet",
                "help": "Account settings → REST API → API Key Management (mailjet.com).",
                "fields": [
                    {"name": "MAILJET_API_KEY", "label": "API key", "secret": True},
                    {"name": "MAILJET_SECRET_KEY", "label": "Secret key", "secret": True},
                    {"name": "MAILJET_FROM_EMAIL", "label": "From email (a verified sender)"},
                    {"name": "MAILJET_FROM_NAME", "label": "From name", "optional": True},
                ],
            },
            "sendgrid": {
                "label": "SendGrid",
                "help": "Settings → API Keys (sendgrid.com). The from address must be a verified sender.",
                "fields": [
                    {"name": "SENDGRID_API_KEY", "label": "API key", "secret": True},
                    {"name": "SENDGRID_FROM_EMAIL", "label": "From email (a verified sender)"},
                    {"name": "SENDGRID_FROM_NAME", "label": "From name", "optional": True},
                ],
            },
        },
    },
    "sms": {
        "selector": "SMS_PROVIDER",
        "getter": get_sms_provider,
        "providers": {
            "twilio": {
                "label": "Twilio",
                "help": "Console home shows the Account SID and Auth Token (twilio.com/console).",
                "fields": [
                    {"name": "TWILIO_ACCOUNT_SID", "label": "Account SID"},
                    {"name": "TWILIO_AUTH_TOKEN", "label": "Auth token", "secret": True},
                    {"name": "TWILIO_FROM_NUMBER", "label": "From number (e.g. +15551234567)"},
                ],
            },
            "infobip": {
                "label": "Infobip",
                "help": "API key and base URL are in your Infobip portal under Developers.",
                "fields": [
                    {"name": "INFOBIP_API_KEY", "label": "API key", "secret": True},
                    {"name": "INFOBIP_BASE_URL", "label": "Base URL (e.g. xxxxx.api.infobip.com)"},
                    {"name": "INFOBIP_FROM", "label": "Sender ID"},
                ],
            },
            "local_modem": {
                "label": "Local modem (self-hosted HTTP API)",
                "help": "The URL and token of your own SMS modem HTTP API.",
                "fields": [
                    {"name": "SMS_API_BASE_URL", "label": "API base URL"},
                    {"name": "SMS_API_TOKEN", "label": "API token", "secret": True},
                ],
            },
        },
    },
    "push": {
        "selector": "PUSH_PROVIDER",
        "getter": get_push_provider,
        "providers": {
            "pushover": {
                "label": "Pushover",
                "help": "User key: pushover.net dashboard. App token: create an application at pushover.net/apps/build.",
                "fields": [
                    {"name": "PUSHOVER_USER_KEY", "label": "User key", "secret": True},
                    {"name": "PUSHOVER_APPTOKEN_DEFAULT", "label": "Application API token", "secret": True},
                ],
                "extra": {"PUSHOVER_APPS": "default:PUSHOVER_APPTOKEN_DEFAULT"},
            },
            "ntfy": {
                "label": "ntfy (free, no account)",
                "help": "Pick a long, hard-to-guess topic name and subscribe to it in the ntfy app.",
                "fields": [
                    {"name": "NTFY_TOPIC", "label": "Topic"},
                    {"name": "NTFY_SERVER_URL", "label": "Server URL (blank = ntfy.sh)", "optional": True},
                    {"name": "NTFY_AUTH_TOKEN", "label": "Access token (only for protected topics)", "secret": True, "optional": True},
                ],
            },
        },
    },
}


def friendly(error: Optional[str]) -> Optional[str]:
    """Turn raw provider errors into something a person can act on."""
    if not error:
        return error
    if "HTTP Error 401" in error or "HTTP Error 403" in error or "HTTP Error 400" in error:
        return "The provider rejected these credentials. Double-check them and try again."
    if "HTTP Error 5" in error:
        return "The provider is having problems right now. Try again in a moment."
    if "URLError" in error or "timed out" in error:
        return "Could not reach the provider. Check your internet connection."
    return error


def safe_check(channel: str, provider_name: Optional[str] = None) -> dict[str, Any]:
    try:
        provider = CATALOG[channel]["getter"](provider_name)
        r = provider.check_config()
        return {"ok": bool(r.ok), "provider": r.provider, "error": friendly(r.error)}
    except SystemExit as e:
        return {"ok": False, "provider": None, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": None, "error": f"Check failed ({type(e).__name__})."}




def public_catalog() -> dict[str, Any]:
    """Catalog without getters/extra wiring, safe to send to the browser."""
    return {
        ch: {
            "label": CHANNEL_LABELS[ch],
            "providers": {
                name: {k: v for k, v in info.items() if k != "extra"}
                for name, info in spec["providers"].items()
            },
        }
        for ch, spec in CATALOG.items()
    }


def default_provider(channel: str) -> str:
    spec = CATALOG[channel]
    return (get_env(spec["selector"], DEFAULT_PROVIDER[channel]) or DEFAULT_PROVIDER[channel]).strip().lower()


def is_field_set(name: str) -> bool:
    return bool(get_env(name))


def provider_connected(channel: str, provider: str) -> bool:
    """True when every required field of the provider has a value."""
    info = CATALOG[channel]["providers"].get(provider)
    if info is None:
        return False
    return all(is_field_set(f["name"]) for f in info["fields"] if not f.get("optional"))


def connected_providers(channel: str) -> list[str]:
    return [name for name in CATALOG[channel]["providers"] if provider_connected(channel, name)]


def provider_status(channel: str, provider: str) -> dict[str, Any]:
    info = CATALOG[channel]["providers"][provider]
    return {
        "name": provider, "label": info["label"], "connected": provider_connected(channel, provider),
        "is_default": provider == default_provider(channel),
        "fields": [{"name": f["name"], "set": is_field_set(f["name"])} for f in info["fields"]],
    }


def channel_status(channel: str) -> dict[str, Any]:
    """Cheap, offline status (no network call).
    state: ready (default provider connected) | needs_attention (some provider
    connected but not the default) | not_set_up (none connected)."""
    spec = CATALOG[channel]
    default = default_provider(channel)
    connected = connected_providers(channel)
    if default in connected:
        state = "ready"
    elif connected:
        state = "needs_attention"
    else:
        state = "not_set_up"
    default_info = spec["providers"].get(default)
    return {
        "channel": channel, "label": CHANNEL_LABELS[channel], "state": state,
        "default": default, "default_label": default_info["label"] if default_info else default,
        "connected": [{"name": n, "label": spec["providers"][n]["label"]} for n in connected],
        "providers": [provider_status(channel, n) for n in spec["providers"]],
    }


def all_status() -> list[dict[str, Any]]:
    return [channel_status(ch) for ch in CATALOG]


def apply_provider(channel: str, provider: str, values: dict[str, str],
                   make_default: Optional[bool] = None) -> dict[str, Any]:
    """Validate, store (encrypted) and verify one provider's settings. Blank values
    keep whatever is already stored, so secrets never need to be re-entered. The
    provider becomes the default if the channel has no working default yet, or if
    make_default is true."""
    spec = CATALOG.get(channel)
    if not spec or provider not in spec["providers"]:
        return {"ok": False, "error": "Unknown channel or provider."}
    info = spec["providers"][provider]

    to_store: dict[str, str] = {}
    missing: list[str] = []
    for field in info["fields"]:
        val = (values.get(field["name"]) or "").strip()
        if val:
            to_store[field["name"]] = val
        elif not field.get("optional") and secret_store.get_setting(field["name"]) is None and not get_env(field["name"]):
            missing.append(field["label"])
    if missing:
        return {"ok": False, "error": "Missing: " + ", ".join(missing)}

    to_store.update(info.get("extra", {}))
    had_working_default = default_provider(channel) in connected_providers(channel)
    if make_default or not had_working_default:
        to_store[spec["selector"]] = provider
    secret_store.set_settings(to_store)
    return safe_check(channel, provider)


def set_default(channel: str, provider: str) -> dict[str, Any]:
    spec = CATALOG.get(channel)
    if not spec or provider not in spec["providers"]:
        return {"ok": False, "error": "Unknown channel or provider."}
    if not provider_connected(channel, provider):
        return {"ok": False, "error": "Connect this provider first."}
    secret_store.set_setting(spec["selector"], provider)
    return {"ok": True}


def remove_provider(channel: str, provider: str) -> dict[str, Any]:
    """Delete a provider's stored credentials. If it was the default, another
    connected provider takes over."""
    spec = CATALOG.get(channel)
    if not spec or provider not in spec["providers"]:
        return {"ok": False, "error": "Unknown channel or provider."}
    info = spec["providers"][provider]
    for f in info["fields"]:
        secret_store.delete_setting(f["name"])
    for extra in info.get("extra", {}):
        secret_store.delete_setting(extra)
    if default_provider(channel) == provider:
        others = [n for n in connected_providers(channel) if n != provider]
        if others:
            secret_store.set_setting(spec["selector"], others[0])
        else:
            secret_store.delete_setting(spec["selector"])
    return {"ok": True}


def send_test(channel: str, to: Optional[str], provider_name: Optional[str] = None) -> dict[str, Any]:
    if channel not in CATALOG:
        return {"ok": False, "error": "Unknown channel."}
    try:
        provider = CATALOG[channel]["getter"](provider_name)
        text = "Test message from your Message Gateway. It works!"
        if channel == "push":
            r = provider.send(body=text, title="Message Gateway", app="default")
        elif channel == "email":
            if not (to or "").strip():
                return {"ok": False, "error": "Enter an email address to send the test to."}
            r = provider.send(to=to.strip(), subject="Message Gateway test", body=text, email_type="txt")
        else:
            if not (to or "").strip():
                return {"ok": False, "error": "Enter a phone number to send the test to."}
            r = provider.send(to=to.strip(), body=text, message_id=str(uuid.uuid4()))
    except SystemExit as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Send failed ({type(e).__name__})."}
    return {"ok": bool(r.ok), "error": friendly(r.error)}
