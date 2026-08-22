"""Delivery channels. Each exposes `configured()`, `verify()` and `send()`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeliveryResult:
    channel: str
    ok: bool
    detail: str = ""
    recipients: int = 0
    messages: int = 0

    def __str__(self) -> str:
        mark = "sent" if self.ok else "FAILED"
        extra = f" ({self.detail})" if self.detail else ""
        return f"{self.channel}: {mark}{extra}"
