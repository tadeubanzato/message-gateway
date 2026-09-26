from app.services.push import get_push_provider
from app.workers.base_worker import run_worker


def _resolve_title(msg: dict) -> str:
    subject = msg.get("subject")
    title = msg.get("title")
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "Message Gateway"


def _deliver(msg: dict):
    meta = msg.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    return get_push_provider((msg.get("provider") or "").strip() or None).send(
        body=msg.get("body", ""),
        title=_resolve_title(msg),
        app=msg.get("app"),
        to=msg.get("to"),
        device=msg.get("device"),
        data=meta,
        url=msg.get("url"),
        url_title=msg.get("url_title"),
    )


if __name__ == "__main__":
    run_worker("push", _deliver, "push-worker")
