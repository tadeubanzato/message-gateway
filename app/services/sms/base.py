from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class SmsResult:
    ok: bool
    provider: str
    provider_message_id: Optional[str] = None
    provider_status: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    raw: Optional[dict[str, Any]] = None
    transient: bool = False


class SmsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def send(self, *, to: str, body: str, message_id: str) -> SmsResult:
        ...

    @abstractmethod
    def required_env_vars(self) -> list[str]:
        ...

    @abstractmethod
    def check_config(self) -> SmsResult:
        ...
