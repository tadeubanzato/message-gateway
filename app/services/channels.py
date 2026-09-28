"""
Channel/provider configuration shared by the first-run setup and the Settings page.

Holds the catalog of channels (email, SMS, push), the providers each supports and
the fields they need; and the operations on them: save credentials (encrypted, via
secret_store), verify them with the provider, send a test, and report status.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from app.services import secret_store
from app.services.email import get_email_provider
from app.services.env import get_env
from app.services.push import get_push_provider
from app.services.sms import get_sms_provider
from app.services.telegram import get_telegram_provider
from app.services.whatsapp import get_whatsapp_provider

DEFAULT_PROVIDER = {"email": "mailjet", "sms": "custom_http", "push": "pushover", "telegram": "bot_api", "whatsapp": "cloud_api"}
CHANNEL_LABELS = {"email": "Email", "sms": "SMS", "push": "Push notifications", "telegram": "Telegram", "whatsapp": "WhatsApp"}

# Display text and badge color for each channel_status() state - defined once
# here so home.html, channel.html and anything else showing a channel's status
# can't drift out of sync with each other.
STATE_LABEL = {
    "not_set_up": "Not set up", "needs_attention": "Needs attention",
    "ready": "Ready", "ready_degraded": "Ready", "disabled": "Disabled",
}
STATE_CSS = {
    "not_set_up": "not_set_up", "needs_attention": "needs_attention",
    "ready": "ready", "ready_degraded": "needs_attention", "disabled": "disabled",
}

# Provider catalog: what the UI asks for. `secret` fields are masked in the UI.
# `extra` values are stored alongside (fixed wiring the user shouldn't have to know).
CATALOG: dict[str, dict[str, Any]] = {
    "email": {
        "selector": "EMAIL_PROVIDER",
        "getter": get_email_provider,
        "providers": {
            "mailjet": {
                "label": "Mailjet",
                "blurb": 'Email delivery API with a free tier. Needs a verified sender address.',
                "links": [{"label": "Sign in to Mailjet", "url": "https://app.mailjet.com/signin"}, {"label": "Open your API keys", "url": "https://app.mailjet.com/account/apikeys"}],
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
                "blurb": 'Email delivery API from Twilio. Needs a verified sender address.',
                "links": [{"label": "Sign in to SendGrid", "url": "https://app.sendgrid.com/login"}, {"label": "Open your API keys", "url": "https://app.sendgrid.com/settings/api_keys"}],
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
            "custom_http": {
                "label": "Custom API endpoint",
                "blurb": 'Setup your own SMS service',
                "help": (
                    "Send through your own SMS service, such as an SMS box or modem API on your network. "
                    "Give the endpoint address and a sample JSON body; the gateway sends every message in that "
                    "pattern, replacing {{to}}, {{message}}, {{message_id}} and {{to_digits}} (the number without the +)."
                ),
                "curl_paste": True,
                "fields": [
                    {"name": "CUSTOM_SMS_URL", "label": "Endpoint URL", "placeholder": "https://sms.example.com/api/send"},
                    {"name": "CUSTOM_SMS_METHOD", "label": "HTTP method", "type": "select",
                     "options": ["POST", "PUT", "PATCH"], "default": "POST", "optional": True},
                    {"name": "CUSTOM_SMS_HEADERS", "label": "Headers (one per line, for example your token)", "type": "textarea",
                     "secret": True, "optional": True, "placeholder": "Authorization: Bearer YOUR_TOKEN"},
                    {"name": "CUSTOM_SMS_BODY", "label": "Request body (JSON sample)", "type": "textarea", "preview": True,
                     "placeholder": '{"number": "{{to}}", "message": "{{message}}"}'},
                    {"name": "CUSTOM_SMS_SUCCESS_TEXT", "label": "Count as delivered only if the response contains",
                     "optional": True, "placeholder": '"status": "ok"'},
                ],
                "example": {
                    "CUSTOM_SMS_METHOD": "POST",
                    "CUSTOM_SMS_HEADERS": "Authorization: Bearer YOUR_TOKEN",
                    "CUSTOM_SMS_BODY": '{\n  "number": "{{to}}",\n  "message": "{{message}}",\n  "message_id": "{{message_id}}"\n}',
                    "CUSTOM_SMS_SUCCESS_TEXT": '"status": "ok"',
                },
            },
            "twilio": {
                "label": "Twilio",
                "blurb": 'Text messages to most countries. Needs a Twilio phone number.',
                "links": [{"label": "Sign in to Twilio", "url": "https://www.twilio.com/login"}, {"label": "Open the Twilio console", "url": "https://console.twilio.com/"}],
                "help": "Console home shows the Account SID and Auth Token (twilio.com/console). The from number must be a phone number you've bought or verified in that account.",
                "fields": [
                    {"name": "TWILIO_ACCOUNT_SID", "label": "Account SID", "placeholder": "AC..."},
                    {"name": "TWILIO_AUTH_TOKEN", "label": "Auth token", "secret": True},
                    {"name": "TWILIO_FROM_NUMBER", "label": "From number (e.g. +15551234567)"},
                ],
            },
            "infobip": {
                "label": "Infobip",
                "blurb": 'Global SMS and messaging platform.',
                "links": [{"label": "Sign in to Infobip", "url": "https://portal.infobip.com/login"}, {"label": "Open your API keys", "url": "https://portal.infobip.com/dev/api-keys"}],
                "help": "API key and base URL are in your Infobip portal under Developers - the base URL is account-specific (looks like xxxxx.api.infobip.com), not the shared api.infobip.com address.",
                "fields": [
                    {"name": "INFOBIP_API_KEY", "label": "API key", "secret": True},
                    {"name": "INFOBIP_BASE_URL", "label": "Base URL (e.g. xxxxx.api.infobip.com)", "placeholder": "https://xxxxx.api.infobip.com"},
                    {"name": "INFOBIP_FROM", "label": "Sender ID or number", "placeholder": "YourBrand"},
                ],
            },
            "sinch": {
                "label": "Sinch",
                "blurb": 'Global SMS API. Needs a Sinch SMS service plan and API token.',
                "links": [{"label": "Sign in to Sinch", "url": "https://dashboard.sinch.com/"}, {"label": "Open SMS API services", "url": "https://dashboard.sinch.com/sms/api/services"}],
                "help": (
                    "Create (or open) an SMS API service under SMS > APIs in the Sinch dashboard for the Service "
                    "Plan ID and an API token. Pick the region the service plan was created in - a mismatch here "
                    "is the most common reason a working token gets rejected."
                ),
                "fields": [
                    {"name": "SINCH_SERVICE_PLAN_ID", "label": "Service Plan ID"},
                    {"name": "SINCH_API_TOKEN", "label": "API token", "secret": True},
                    {"name": "SINCH_REGION", "label": "Region", "type": "select",
                     "options": ["us", "eu", "au", "br", "ca"], "default": "us", "optional": True},
                    {"name": "SINCH_FROM_NUMBER", "label": "From number or sender ID", "placeholder": "+15551234567"},
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
                "blurb": 'Push notifications to your phone or desktop. Needs a Pushover account.',
                "links": [{"label": "Sign in to Pushover", "url": "https://pushover.net/login"}, {"label": "Create an application", "url": "https://pushover.net/apps/build"}],
                "help": "Enter your Pushover user key (shown on your pushover.net dashboard), then the API token of your default app. Use + to add more apps.",
                "fields": [
                    {"name": "PUSHOVER_USER_KEY", "label": "User key", "secret": True},
                ],
                "apps": True,  # plus any number of named applications, each with its own token
            },
            "ntfy": {
                "label": "ntfy (free, no account)",
                "blurb": 'Free push notifications with no account. Works with the ntfy app.',
                "links": [{"label": "Open ntfy", "url": "https://ntfy.sh/app"}, {"label": "ntfy documentation", "url": "https://docs.ntfy.sh/"}],
                "help": "Pick a long, hard-to-guess topic name and subscribe to it in the ntfy app.",
                "fields": [
                    {"name": "NTFY_TOPIC", "label": "Topic"},
                    {"name": "NTFY_SERVER_URL", "label": "Server URL (blank = ntfy.sh)", "optional": True},
                    {"name": "NTFY_AUTH_TOKEN", "label": "Access token (only for protected topics)", "secret": True, "optional": True},
                ],
            },
        },
    },
    "telegram": {
        "selector": "TELEGRAM_PROVIDER",
        "getter": get_telegram_provider,
        "providers": {
            "bot_api": {
                "label": "Telegram Bot",
                "blurb": 'Send messages via a bot you create. Free, no phone number needed.',
                "links": [
                    {"label": "Open @BotFather", "url": "https://t.me/BotFather"},
                    {"label": "Telegram Bot API docs", "url": "https://core.telegram.org/bots/api"},
                ],
                "help": (
                    "In Telegram, open @BotFather and send /newbot. Follow the prompts to name your bot; "
                    "BotFather replies with a token that looks like 123456789:AAH... - paste it below. "
                    "Telegram bots can't message someone first: whoever should receive messages has to "
                    "open your bot and send it anything (even just \"hi\") once. After that, save your "
                    "token here and use Find chat IDs to look up theirs."
                ),
                "fields": [
                    {"name": "TELEGRAM_BOT_TOKEN", "label": "Bot token", "secret": True, "placeholder": "123456789:AAH-your-bot-token"},
                ],
            },
        },
    },
    "whatsapp": {
        "selector": "WHATSAPP_PROVIDER",
        "getter": get_whatsapp_provider,
        "providers": {
            "cloud_api": {
                "label": "WhatsApp Business Platform",
                "blurb": 'Send via Meta’s official WhatsApp Cloud API. Needs a Meta Business/developer setup.',
                "links": [
                    {"label": "Meta for Developers", "url": "https://developers.facebook.com/"},
                    {"label": "WhatsApp Cloud API: Get started", "url": "https://developers.facebook.com/docs/whatsapp/cloud-api/get-started"},
                    {"label": "Business Settings (for a permanent token)", "url": "https://business.facebook.com/settings"},
                ],
                "help": (
                    "Create a Meta app, add the WhatsApp product, and note the Phone Number ID from its API "
                    "Setup page. Access tokens issued there expire in 24 hours - for a token that keeps "
                    "working, create a System User under Business Settings > System users, assign it the app "
                    "and the WhatsApp Business Account with full control, and generate its token there instead. "
                    "Important: WhatsApp only delivers a free-form message if that person has messaged your "
                    "WhatsApp number in the last 24 hours - anyone else needs a pre-approved message template, "
                    "which isn't supported here."
                ),
                "fields": [
                    {"name": "WHATSAPP_PHONE_NUMBER_ID", "label": "Phone number ID"},
                    {"name": "WHATSAPP_ACCESS_TOKEN", "label": "Access token", "secret": True},
                    {"name": "WHATSAPP_API_VERSION", "label": "Graph API version", "placeholder": "v26.0", "optional": True},
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


def telegram_recent_chats() -> dict[str, Any]:
    """Chat ids that have recently messaged the saved bot, for the "Find chat IDs" helper.
    Requires a bot token to already be saved (Channels > Telegram)."""
    from app.services.telegram.bot_api import recent_chats

    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "Save a bot token first."}
    try:
        return {"ok": True, "chats": recent_chats(token)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": friendly(str(e))}




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


# ---------------------------------------------------------------------
# Pushover applications: any number of named apps, each with its own token.
# Stored in the same format the provider reads:
#   PUSHOVER_APPS=alerts:PUSHOVER_APPTOKEN_ALERTS,backups:PUSHOVER_APPTOKEN_BACKUPS
#   PUSHOVER_APPTOKEN_ALERTS=<token>    (each token encrypted separately)
#   PUSHOVER_DEFAULT_APP=<name>          (used when a message names no app)
# ---------------------------------------------------------------------
APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")


def _app_env_var(name: str) -> str:
    return "PUSHOVER_APPTOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _parse_apps(raw: Optional[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in (raw or "").split(","):
        if ":" in pair:
            n, v = pair.split(":", 1)
            if n.strip() and v.strip():
                mapping[n.strip().lower()] = v.strip()
    return mapping


def pushover_apps() -> list[dict[str, Any]]:
    """Configured Pushover apps: name, whether its token is set, and which is the default."""
    mapping = _parse_apps(get_env("PUSHOVER_APPS"))
    chosen = (get_env("PUSHOVER_DEFAULT_APP") or "").strip().lower()
    default = chosen if chosen in mapping else ("default" if "default" in mapping else next(iter(mapping), ""))
    return [
        {"name": n, "set": is_field_set(env), "is_default": n == default, "last4": mask_tail(get_env(env))}
        for n, env in mapping.items()
    ]


def _save_pushover_apps(apps: list[dict[str, str]], default_app: Optional[str]) -> Optional[str]:
    """Replace the app list. Blank tokens keep an existing app's saved token. Returns
    an error message, or None on success."""
    existing = _parse_apps(get_env("PUSHOVER_APPS"))
    final: dict[str, str] = {}
    tokens: dict[str, str] = {}
    used_env: set[str] = set()
    for item in apps:
        name = (item.get("name") or "").strip().lower()
        token = (item.get("token") or "").strip()
        if not name and not token:
            continue  # blank row
        if not APP_NAME_RE.match(name):
            return f"Invalid app name {name!r}. Use 1 to 40 letters, numbers, dots, dashes or underscores."
        if name in final:
            return f"App name {name!r} is used twice."
        env = existing.get(name) or _app_env_var(name)
        if env in used_env:
            return f"App names {name!r} and another one map to the same setting. Use a different name."
        if not token and not is_field_set(env):
            return f"App {name!r} needs an API token."
        final[name] = env
        used_env.add(env)
        if token:
            tokens[env] = token
    if not final:
        return "Add at least one application."
    for name, env in existing.items():  # apps that were removed: forget their tokens
        if name not in final and env not in used_env:
            secret_store.delete_setting(env)
    chosen = (default_app or "").strip().lower()
    if chosen not in final:
        prev = (get_env("PUSHOVER_DEFAULT_APP") or "").strip().lower()
        chosen = prev if prev in final else ("default" if "default" in final else next(iter(final)))
    secret_store.set_settings({**tokens, "PUSHOVER_APPS": ",".join(f"{n}:{e}" for n, e in final.items()),
                               "PUSHOVER_DEFAULT_APP": chosen})
    return None


def env_locked(names: list[str]) -> list[str]:
    """Settings currently pinned by an environment variable (which overrides what is saved here)."""
    import os

    return [n for n in names if (os.environ.get(n) or "").strip()]


# Settings that belong to a whole channel rather than one provider.
CHANNEL_DEFAULTS: dict[str, list[dict[str, Any]]] = {
    "sms": [
        {"name": "SMS_DEFAULT_TO", "label": "Default phone number",
         "help": "Used when a message doesn't say who to text. Include the country code.",
         "placeholder": "+15551234567", "kind": "phone"},
    ],
    "telegram": [
        {"name": "TELEGRAM_DEFAULT_CHAT_ID", "label": "Default chat ID",
         "help": "Used when a message doesn't say who to notify. Use Find chat IDs above to look yours up.",
         "placeholder": "123456789"},
    ],
    "whatsapp": [
        {"name": "WHATSAPP_DEFAULT_TO", "label": "Default phone number",
         "help": "Used when a message doesn't say who to notify. Include the country code. They must have messaged your WhatsApp number in the last 24 hours.",
         "placeholder": "+15551234567", "kind": "phone"},
    ],
}


def default_sms_number() -> Optional[str]:
    return (get_env("SMS_DEFAULT_TO") or "").strip() or None


def default_telegram_chat_id() -> Optional[str]:
    return (get_env("TELEGRAM_DEFAULT_CHAT_ID") or "").strip() or None


def default_whatsapp_number() -> Optional[str]:
    return (get_env("WHATSAPP_DEFAULT_TO") or "").strip() or None


def channel_defaults(channel: str) -> list[dict[str, Any]]:
    return [
        {**spec, "value": get_env(spec["name"]) or ""} for spec in CHANNEL_DEFAULTS.get(channel, [])
    ]


def save_defaults(channel: str, values: dict[str, str]) -> dict[str, Any]:
    """Save channel-level defaults (currently the default SMS phone number). A blank
    value clears the setting."""
    from app.services.phone import normalize_phone

    specs = {sp["name"]: sp for sp in CHANNEL_DEFAULTS.get(channel, [])}
    for name, raw in values.items():
        spec = specs.get(name)
        if spec is None:
            return {"ok": False, "error": f"Unknown setting {name}."}
        raw = (raw or "").strip()
        if not raw:
            secret_store.delete_setting(name)
            continue
        if spec.get("kind") == "phone":
            try:
                raw = normalize_phone(raw)
            except ValueError as e:
                return {"ok": False, "error": str(e)}
        secret_store.set_setting(name, raw)
    return {"ok": True}


def mask_tail(value: Optional[str]) -> Optional[str]:
    """Last 4 characters of a saved credential, so it can be compared with the one in the
    provider's dashboard. Short secrets show nothing, so a short secret is never mostly revealed."""
    v = (value or "").strip()
    return v[-4:] if len(v) >= 12 else None


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
    if not all(is_field_set(f["name"]) for f in info["fields"] if not f.get("optional")):
        return False
    if info.get("apps"):
        return any(a["set"] for a in pushover_apps())
    return True


def connected_providers(channel: str) -> list[str]:
    return [name for name in CATALOG[channel]["providers"] if provider_connected(channel, name)]


def provider_enabled(channel: str, provider: str) -> bool:
    """Whether this specific connected provider is allowed to send. On by default;
    turning it off blocks a send that would use it, whether picked explicitly or
    as the channel's default (see enqueue_message) - the provider stays connected
    and configured, it's just not allowed to send."""
    return (get_env(f"PROVIDER_ENABLED_{channel.upper()}_{provider.upper()}", "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def set_provider_enabled(channel: str, provider: str, enabled: bool) -> None:
    secret_store.set_setting(f"PROVIDER_ENABLED_{channel.upper()}_{provider.upper()}", "1" if enabled else "0")


def provider_status(channel: str, provider: str) -> dict[str, Any]:
    info = CATALOG[channel]["providers"][provider]
    out = {
        "name": provider, "label": info["label"], "connected": provider_connected(channel, provider),
        "is_default": provider == default_provider(channel),
        "enabled": provider_enabled(channel, provider),
        "fields": [
            {"name": f["name"], "set": is_field_set(f["name"]),
             "value": None if f.get("secret") else (get_env(f["name"]) or ""),
             "last4": mask_tail(get_env(f["name"])) if f.get("secret") else None}
            for f in info["fields"]
        ],
        "env_locked": env_locked([f["name"] for f in info["fields"]] + (["PUSHOVER_APPS"] if info.get("apps") else [])),
    }
    if info.get("apps"):
        out["apps"] = pushover_apps()
    return out


def channel_status(channel: str) -> dict[str, Any]:
    """Cheap, offline status (no network call).
    state: not_set_up (nothing connected) | needs_attention (something connected
    but not the default) | ready (default connected, every connected provider
    enabled) | ready_degraded (default connected, some connected providers
    turned off - same green/ready meaning, shown in amber) | disabled (default
    connected, every connected provider turned off)."""
    spec = CATALOG[channel]
    default = default_provider(channel)
    connected = connected_providers(channel)
    if default not in connected:
        state = "needs_attention" if connected else "not_set_up"
    else:
        enabled_count = sum(1 for n in connected if provider_enabled(channel, n))
        if enabled_count == len(connected):
            state = "ready"
        elif enabled_count == 0:
            state = "disabled"
        else:
            state = "ready_degraded"
    default_info = spec["providers"].get(default)
    return {
        "channel": channel, "label": CHANNEL_LABELS[channel], "state": state,
        # Text and badge color computed once here, not duplicated per template -
        # "ready_degraded" reads as "Ready" (it still sends) but colors like
        # "needs_attention" (amber), so it's visibly not the full-green state.
        "state_label": STATE_LABEL[state], "state_css": STATE_CSS[state],
        "default": default, "default_label": default_info["label"] if default_info else default,
        "connected": [{"name": n, "label": spec["providers"][n]["label"]} for n in connected],
        "providers": [provider_status(channel, n) for n in spec["providers"]],
        "defaults": channel_defaults(channel),
    }


def all_status() -> list[dict[str, Any]]:
    return [channel_status(ch) for ch in CATALOG]


def apply_provider(channel: str, provider: str, values: dict[str, str],
                   make_default: Optional[bool] = None,
                   apps: Optional[list[dict[str, str]]] = None,
                   default_app: Optional[str] = None) -> dict[str, Any]:
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

    if provider == "custom_http":
        from app.services.sms import custom_http

        merged = {f["name"]: (values.get(f["name"]) or get_env(f["name"]) or "") for f in info["fields"]}
        problem = custom_http.validate_config(
            merged["CUSTOM_SMS_URL"], merged["CUSTOM_SMS_METHOD"] or "POST",
            merged["CUSTOM_SMS_HEADERS"], merged["CUSTOM_SMS_BODY"],
        )
        if problem:
            return {"ok": False, "error": problem}

    if info.get("apps"):
        if apps is None and not pushover_apps():
            return {"ok": False, "error": "Add at least one application."}
        if apps is not None:
            err = _save_pushover_apps(apps, default_app)
            if err:
                return {"ok": False, "error": err}
        elif default_app:
            secret_store.set_setting("PUSHOVER_DEFAULT_APP", default_app.strip().lower())

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
    if info.get("apps"):
        for a in pushover_apps():
            secret_store.delete_setting(_parse_apps(get_env("PUSHOVER_APPS")).get(a["name"], _app_env_var(a["name"])))
        secret_store.delete_setting("PUSHOVER_APPS")
        secret_store.delete_setting("PUSHOVER_DEFAULT_APP")
    if default_provider(channel) == provider:
        others = [n for n in connected_providers(channel) if n != provider]
        if others:
            secret_store.set_setting(spec["selector"], others[0])
        else:
            secret_store.delete_setting(spec["selector"])
    return {"ok": True}


# ---------------------------------------------------------------------
# Import from an existing .env file (migrating an older install)
# ---------------------------------------------------------------------
_LEGACY_MODEM_BODY = '{\n  "number": "{{to}}",\n  "message": "{{message}}",\n  "message_id": "{{message_id}}"\n}'


def _parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # trailing comment on an unquoted value
        out[key] = value
    return out


def import_env_text(text: str) -> dict[str, Any]:
    """Save the provider settings found in .env text into the encrypted store.
    Returns which providers ended up connected, plus names (never values) of settings
    that were imported or skipped."""
    env = {k: v for k, v in _parse_env_text(text).items() if v}
    known: set[str] = {"SMS_DEFAULT_TO", "PUSHOVER_APPS", "PUSHOVER_DEFAULT_APP"}
    for spec in CATALOG.values():
        for info in spec["providers"].values():
            known.update(f["name"] for f in info["fields"])
    to_store: dict[str, str] = {k: v for k, v in env.items() if k in known}

    # Pushover: also take the token variable each app points at (any variable names).
    apps = _parse_apps(env.get("PUSHOVER_APPS"))
    for var in apps.values():
        if var in env and re.fullmatch(r"[A-Z0-9_]{1,80}", var):
            to_store[var] = env[var]
    if env.get("PUSHOVER_USER_KEY") and not apps and env.get("PUSHOVER_APPTOKEN"):
        to_store["PUSHOVER_APPS"] = "default:PUSHOVER_APPTOKEN_DEFAULT"
        to_store["PUSHOVER_APPTOKEN_DEFAULT"] = env["PUSHOVER_APPTOKEN"]

    # Older installs used a fixed "local modem" SMS API: map it to the custom endpoint.
    if env.get("SMS_API_BASE_URL") and env.get("SMS_API_TOKEN") and not env.get("CUSTOM_SMS_URL"):
        to_store.update({
            "CUSTOM_SMS_URL": env["SMS_API_BASE_URL"].rstrip("/") + "/api/send_sms",
            "CUSTOM_SMS_METHOD": "POST",
            "CUSTOM_SMS_HEADERS": f"Authorization: Bearer {env['SMS_API_TOKEN']}",
            "CUSTOM_SMS_BODY": _LEGACY_MODEM_BODY,
            "CUSTOM_SMS_SUCCESS_TEXT": '"status": "ok"',
        })
        if (env.get("SMS_PROVIDER") or "local_modem").lower() in ("local_modem", "custom_http"):
            to_store["SMS_PROVIDER"] = "custom_http"

    # Provider choices: honour the selected default when it names a provider we know.
    consumed = {"SMS_API_BASE_URL", "SMS_API_TOKEN"} if "CUSTOM_SMS_URL" in to_store and "SMS_API_BASE_URL" in env else set()
    for ch, spec in CATALOG.items():
        chosen = (env.get(spec["selector"]) or "").strip().lower()
        if chosen in spec["providers"] and spec["selector"] not in to_store:
            to_store[spec["selector"]] = chosen
        if spec["selector"] in env:
            consumed.add(spec["selector"])

    secret_store.set_settings(to_store)

    # Make sure each channel with a connected provider has a working default.
    for ch, spec in CATALOG.items():
        connected = connected_providers(ch)
        if connected and default_provider(ch) not in connected:
            secret_store.set_setting(spec["selector"], connected[0])

    skipped = sorted(k for k in env if k not in to_store and k not in consumed)
    return {
        "ok": True,
        "connected": {ch: connected_providers(ch) for ch in CATALOG if connected_providers(ch)},
        "imported": sorted(to_store),          # setting names only, never values
        "skipped": skipped,                    # e.g. infrastructure settings managed automatically
    }
