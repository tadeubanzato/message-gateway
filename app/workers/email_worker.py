from app.services.email import get_email_provider
from app.workers.base_worker import run_worker


def _deliver(msg: dict):
    email_type = (msg.get("emailType") or "txt").strip().lower()
    if email_type not in ("txt", "html"):
        email_type = "txt"
    return get_email_provider((msg.get("provider") or "").strip() or None).send(
        to=(msg.get("to") or "").strip(),
        subject=(msg.get("subject") or "Message Gateway").strip(),
        body=msg.get("body", "") or "",
        email_type=email_type,
        message_id=(msg.get("message_id") or "").strip(),
    )


if __name__ == "__main__":
    run_worker("email", _deliver, "email-worker")
