from app.services.telegram import get_telegram_provider
from app.workers.base_worker import run_worker


def _deliver(msg: dict):
    return get_telegram_provider((msg.get("provider") or "").strip() or None).send(
        to=msg.get("to", ""),
        body=msg.get("body", ""),
        message_id=msg.get("message_id", ""),
    )


if __name__ == "__main__":
    run_worker("telegram", _deliver, "telegram-worker")
