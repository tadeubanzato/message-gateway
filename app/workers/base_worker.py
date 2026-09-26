"""
Shared worker loop: consumes from a channel's queue, calls the active
provider, persists a delivery attempt, retries transient failures with
backoff, and nacks-without-requeue permanent failures (which routes them to
that channel's dead-letter queue via the DLX binding set up in broker.py).
"""

from __future__ import annotations

import json
import time
from typing import Callable

import pika

from app.broker import EXCHANGE, EXCHANGE_TYPE, QUEUE_NAMES, RABBITMQ_URL, ROUTING_KEY, declare_topology
from app.db import get_repository
from app.services import message_log

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 2


def _connect():
    params = pika.URLParameters(RABBITMQ_URL)
    return pika.BlockingConnection(params)


def run_worker(channel_name: str, deliver_fn: Callable[[dict], object], label: str) -> None:
    """
    deliver_fn(msg_dict) -> a Result-like object with .ok, .transient, .error,
    .provider, .provider_message_id, .status_code (EmailResult/SmsResult/PushResult
    all share this shape).
    """
    queue_name = QUEUE_NAMES[channel_name]
    print(f"[{label}] starting... queue={queue_name}")

    while True:
        try:
            conn = _connect()
            ch = conn.channel()
            declare_topology(ch)
            ch.basic_qos(prefetch_count=1)

            def on_message(channel, method, properties, body: bytes):
                try:
                    msg = json.loads(body.decode("utf-8", errors="replace"))
                except Exception as e:
                    print(f"[{label}] bad message body, dropping to DLQ: {e!r}")
                    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return

                if msg.get("channel") != channel_name:
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                    return

                message_id = (msg.get("message_id") or "").strip()
                attempt_count = int(msg.get("_attempt_count", 0)) + 1

                try:
                    result = deliver_fn(msg)
                except Exception as e:
                    print(f"[{label}] ❌ exception delivering message_id={message_id}: {e!r}")
                    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    return

                try:
                    get_repository().insert_attempt({
                        "message_id": message_id,
                        "provider": getattr(result, "provider", None),
                        "ok": bool(result.ok),
                        "status_code": getattr(result, "status_code", None),
                        "error": getattr(result, "error", None),
                        "provider_message_id": getattr(result, "provider_message_id", None),
                        "attempt_number": attempt_count,
                    })
                except Exception as e:
                    print(f"[{label}] warning: failed to persist attempt record: {e!r}")
                message_log.record_attempt(
                    message_id, attempt=attempt_count,
                    provider=getattr(result, "provider", None),
                    provider_message_id=getattr(result, "provider_message_id", None),
                    error=getattr(result, "error", None),
                )

                if result.ok:
                    print(f"[{label}] ✅ sent message_id={message_id} provider_id={getattr(result, 'provider_message_id', None)}")
                    message_log.record_final(message_id, "delivered")
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                    return

                transient = bool(getattr(result, "transient", False))
                if transient and attempt_count < MAX_ATTEMPTS:
                    backoff = BACKOFF_BASE_SECONDS ** attempt_count
                    print(
                        f"[{label}] transient failure (attempt {attempt_count}/{MAX_ATTEMPTS}), "
                        f"retrying in {backoff}s: {getattr(result, 'error', None)}"
                    )
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                    time.sleep(backoff)
                    retry_msg = dict(msg)
                    retry_msg["_attempt_count"] = attempt_count
                    channel.basic_publish(
                        exchange=EXCHANGE, routing_key=ROUTING_KEY,
                        body=json.dumps(retry_msg).encode("utf-8"),
                        properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                    )
                    return

                print(f"[{label}] ❌ permanent failure message_id={message_id}: {getattr(result, 'error', None)}")
                message_log.record_final(message_id, "failed")
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            ch.basic_consume(queue=queue_name, on_message_callback=on_message, auto_ack=False)
            print(f"[{label}] consuming...")
            ch.start_consuming()

        except Exception as e:
            print(f"[{label}] connection loop error: {e!r}")
            time.sleep(2)
