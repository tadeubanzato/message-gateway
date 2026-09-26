from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PushResult:
    ok: bool
    provider: str
    provider_message_id: Optional[str] = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    raw: Optional[dict[str, Any]] = None
    transient: bool = False


class PushProvider(ABC):
    name: str = "base"

    @abstractmethod
    def send(
        self, *, body: str, title: Optional[str] = None, device: Optional[str] = None,
        app: Optional[str] = None, to: Optional[str] = None, data: Optional[dict[str, Any]] = None,
        url: Optional[str] = None, url_title: Optional[str] = None,
    ) -> PushResult:
        ...

    @abstractmethod
    def required_env_vars(self) -> list[str]:
        ...

    @abstractmethod
    def check_config(self) -> PushResult:
        ...
