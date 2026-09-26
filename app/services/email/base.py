from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class EmailResult:
    ok: bool
    provider: str
    provider_message_id: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None
    # Set True for errors worth retrying (5xx/timeout); False for permanent
    # (4xx/bad-input) failures. Consumed by the worker's retry-vs-DLQ logic.
    transient: bool = False


class EmailProvider(ABC):
    name: str = "base"

    @abstractmethod
    def send(
        self, *, to: str, subject: str, body: str, email_type: str = "txt",
        message_id: Optional[str] = None,
    ) -> EmailResult:
        ...

    @abstractmethod
    def required_env_vars(self) -> list[str]:
        """Env var names this provider needs — used by the MCP wizard's
        get_setup_instructions, never to accept secret values as arguments."""
        ...

    @abstractmethod
    def check_config(self) -> EmailResult:
        """Lightweight credential-validity check against the live provider API.
        Used by the MCP wizard's check_provider_config tool."""
        ...
