"""
py-stream-and-billing-guard
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Defensive engineering primitives for Python & FastAPI services:
1. SSEFrameAssembler: Reassembles fragmented TCP/HTTP chunks into complete SSE
   frames before guardrail or parser evaluation.
2. CurrencyNormalizer: Fail-closed minor-unit & zero-decimal currency normalizer
   (preventing 100x overcharges on JPY/KRW/VND and float rounding drift).
3. commit_before_external_cleanup: Enforces DB persistence before irreversible
   S3/Stripe external deletions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping


class BillingValidationError(ValueError):
    """Raised when a currency, plan, or amount fails fail-closed validation."""


# ISO-4217 Zero-Decimal currencies where 1 unit is already the smallest minor unit
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)


@dataclass(frozen=True)
class FormattedPrice:
    currency: str
    minor_units: int
    display: str


class CurrencyNormalizer:
    """
    Fail-closed currency formatter and minor-unit validator derived dynamically
    from a canonical price catalog.
    """

    def __init__(self, canonical_catalog: Mapping[str, int]) -> None:
        if not canonical_catalog:
            raise BillingValidationError("Canonical price catalog cannot be empty")
        self._catalog: dict[str, int] = {
            k.strip().upper(): int(v) for k, v in canonical_catalog.items()
        }

    @property
    def supported_currencies(self) -> frozenset[str]:
        return frozenset(self._catalog.keys())

    def resolve_minor_units(self, currency: str) -> int:
        code = (currency or "").strip().upper()
        if code not in self._catalog:
            raise BillingValidationError(f"Unsupported or unconfigured currency: {currency!r}")
        amount = self._catalog[code]
        if amount <= 0:
            raise BillingValidationError(f"Minor-unit price must be positive for {code}")
        return amount

    def format_minor_units(self, currency: str, minor_units: int | None = None) -> FormattedPrice:
        code = (currency or "").strip().upper()
        units = self.resolve_minor_units(code) if minor_units is None else int(minor_units)
        if units < 0:
            raise BillingValidationError("Minor units cannot be negative")

        if code in ZERO_DECIMAL_CURRENCIES:
            display = f"{units:,} {code}"
        else:
            major = units / 100.0
            display = f"{major:,.2f} {code}"
        return FormattedPrice(currency=code, minor_units=units, display=display)


class SSEFrameAssembler:
    """
     stateful Server-Sent Events (SSE) stream framing normalizer.

    Buffers arbitrary incoming transport chunks and yields only complete logical
    SSE event blocks (delimited by `\n\n` or `\r\n\r\n`), preventing split-chunk
    framing bypasses in downstream AI safety guardrails.
    """

    def __init__(self, max_buffer_chars: int = 262_144) -> None:
        self._buffer: str = ""
        self._max_buffer_chars = max_buffer_chars

    def feed(self, chunk: str | bytes) -> list[str]:
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8", errors="replace")
        else:
            text = str(chunk)

        normalized = text.replace("\r\n", "\n")
        self._buffer += normalized
        if len(self._buffer) > self._max_buffer_chars:
            raise ValueError("SSE frame buffer exceeded maximum allowed size")

        complete_frames: list[str] = []
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            if frame.strip():
                complete_frames.append(frame)
        return complete_frames

    def flush(self) -> list[str]:
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []

    @staticmethod
    def extract_data_payload(frames: Iterable[str]) -> str:
        """Concatenates `data:` lines across one or more complete SSE frames."""
        parts: list[str] = []
        for frame in frames:
            for line in frame.split("\n"):
                if line.startswith("data:"):
                    parts.append(line[5:].lstrip(" "))
        return "".join(parts)


def commit_before_external_cleanup(
    save_fn: Callable[[], bool],
    external_cleanup_fn: Callable[[], None],
) -> bool:
    """
    Executes `save_fn()` first and invokes `external_cleanup_fn()` (e.g., S3 asset
    deletion or Stripe schedule release) ONLY when the database commit succeeds.
    """
    committed = bool(save_fn())
    if not committed:
        return False
    external_cleanup_fn()
    return True
