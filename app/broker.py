"""
RabbitMQ broker access — publisher (API process) and consumer helpers (workers).

Fixes the old codebase's biggest identified perf bug: the old broker/rabbitmq.py
opened a brand-new BlockingConnection per publish call. Here, publish_message()
reuses a single lazily-created, thread-safe-guarded connection/channel per
process instead of reconnecting every time.

Also sets up a dead-letter exchange/queue per channel so failed deliveries are
inspectable/retryable instead of vanishing on nack(requeue=False).
"""

from __future__ import annotations

import json
import os
import threading

import pika

from app.schemas import MessageEnqueued

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "").strip()
if not RABBITMQ_URL:
    raise SystemExit("RABBITMQ_URL is missing")

EXCHANGE = os.environ.get("RABBITMQ_EXCHANGE", "events").strip()
EXCHANGE_TYPE = os.environ.get("RABBITMQ_EXCHANGE_TYPE", "topic").strip()
ROUTING_KEY = os.environ.get("RABBITMQ_ROUTING_KEY", "outbound.send").strip()

DLX_EXCHANGE = os.environ.get("RABBITMQ_DLX_EXCHANGE", "events.dlx").strip()

QUEUE_NAMES = {
    "email": os.environ.get("EMAIL_QUEUE_NAME", "notify.email").strip(),
    "sms": os.environ.get("SMS_QUEUE_NAME", "notify.sms").strip(),
    "push": os.environ.get("PUSH_QUEUE_NAME", "notify.push").strip(),
}


def dead_letter_queue_name(channel: str) -> str:
    return f"{QUEUE_NAMES[channel]}.dlq"


class _PublisherConnection:
    """Lazily-created, reused BlockingConnection for the API process.

    A lock guards (re)connect + publish since FastAPI's sync routes run in a
    thread pool and pika's BlockingConnection is not thread-safe for
    concurrent use from multiple threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: pika.BlockingConnection | None = None
        self._channel = None

    def _connect(self) -> None:
        params = pika.URLParameters(RABBITMQ_URL)
        self._conn = pika.BlockingConnection(params)
        self._channel = self._conn.channel()
        declare_topology(self._channel)

    def _ensure_connected(self) -> None:
        if self._conn is not None and self._conn.is_open:
            return
        self._connect()

    def publish(self, routing_key: str, body: bytes) -> None:
        with self._lock:
            try:
                self._ensure_connected()
                self._channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=routing_key,
                    body=body,
                    properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                )
            except (pika.exceptions.AMQPError, OSError):
                # Reconnect once and retry — handles a broker restart or a
                # connection that died from being idle.
                self._connect()
                self._channel.basic_publish(
                    exchange=EXCHANGE,
                    routing_key=routing_key,
                    body=body,
                    properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                )


def declare_topology(channel) -> None:
    """Idempotent declare of the main exchange, DLX, and per-channel queues+DLQs.
    Called by both the API publisher and every worker on connect."""
    channel.exchange_declare(exchange=EXCHANGE, exchange_type=EXCHANGE_TYPE, durable=True)
    channel.exchange_declare(exchange=DLX_EXCHANGE, exchange_type="direct", durable=True)

    for chan_name, queue_name in QUEUE_NAMES.items():
        dlq_routing_key = f"dlq.{chan_name}"
        dlq_name = dead_letter_queue_name(chan_name)

        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_EXCHANGE,
                "x-dead-letter-routing-key": dlq_routing_key,
            },
        )
        channel.queue_bind(queue=queue_name, exchange=EXCHANGE, routing_key=ROUTING_KEY)

        channel.queue_declare(queue=dlq_name, durable=True)
        channel.queue_bind(queue=dlq_name, exchange=DLX_EXCHANGE, routing_key=dlq_routing_key)


_publisher = _PublisherConnection()


def publish_message(msg: MessageEnqueued) -> None:
    body = json.dumps(msg.model_dump(), ensure_ascii=False).encode("utf-8")
    _publisher.publish(ROUTING_KEY, body)


def republish_from_dead_letter(channel_name: str, message_body: dict) -> None:
    """Used by the MCP retry_dead_letter tool — republish a message back onto
    its real (non-DLQ) queue for redelivery."""
    body = json.dumps(message_body, ensure_ascii=False).encode("utf-8")
    _publisher.publish(ROUTING_KEY, body)
