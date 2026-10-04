from app.services.whatsapp import get_whatsapp_provider
from app.workers.base_worker import run_worker


def _deliver(msg: dict):
    return get_whatsapp_provider((msg.get("provider") or "").strip() or None).send(
        to=msg.get("to", ""),
        body=msg.get("body", ""),
        message_id=msg.get("message_id", ""),
        account=(msg.get("app") or None),   # the API's `account` travels in the queue message's `app`
    )


if __name__ == "__main__":
    run_worker("whatsapp", _deliver, "whatsapp-worker")
