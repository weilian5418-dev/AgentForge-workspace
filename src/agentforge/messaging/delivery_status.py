"""Transmission vs reply status — pure mapping (D09.1).

awaiting_reply must not overwrite the fact that a message was already received.
sent ≠ received ≠ completed.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentforge.contracts.messages import (
    DeliveryStatus,
    ReplyStatus,
    TransmissionStatus,
)

# Legacy flat DeliveryStatus → (transmission, reply).
LEGACY_DELIVERY_STATUS_MAP: dict[str, tuple[TransmissionStatus, ReplyStatus]] = {
    "pending": ("pending", "not_required"),
    "dispatching": ("claimed", "not_required"),
    "sent": ("sent", "not_required"),
    "received": ("received", "not_required"),
    "awaiting_reply": ("received", "awaiting_reply"),
    "responded": ("received", "responded"),
    "failed": ("failed", "not_required"),
    "expired": ("expired", "not_required"),
    "unknown": ("unknown", "not_required"),
}


@dataclass(frozen=True)
class DeliveryStatusFacets:
    transmission: TransmissionStatus
    reply: ReplyStatus


def split_legacy_delivery_status(status: DeliveryStatus | str) -> DeliveryStatusFacets:
    mapped = LEGACY_DELIVERY_STATUS_MAP.get(status)
    if mapped is None:
        raise ValueError(f"Unsupported legacy delivery status: {status}")
    transmission, reply = mapped
    return DeliveryStatusFacets(transmission=transmission, reply=reply)


def combine_delivery_status_facets(
    transmission: TransmissionStatus,
    reply: ReplyStatus,
) -> DeliveryStatus:
    """Best-effort reverse map for migration readers. Not a source of truth."""
    if reply == "awaiting_reply":
        return "awaiting_reply"
    if reply == "responded":
        return "responded"
    if reply == "timed_out" and transmission == "received":
        return "expired"
    if transmission == "claimed":
        return "dispatching"
    if transmission in (
        "pending",
        "sent",
        "received",
        "unknown",
        "failed",
        "expired",
    ):
        return transmission  # type: ignore[return-value]
    return "unknown"


def reply_must_not_downgrade_transmission(
    transmission: TransmissionStatus,
    reply: ReplyStatus,
) -> bool:
    """True when the pair is coherent: reply waits only after received."""
    if reply in ("awaiting_reply", "responded", "timed_out"):
        return transmission == "received"
    return True
