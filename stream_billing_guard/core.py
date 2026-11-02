"""
py-stream-and-billing-guard (v0.3.0)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Defensive engineering primitives for Python & FastAPI services:
1. SSEFrameAssembler & scan_sse_stream_for_leaks: Reassembles fragmented TCP/HTTP
   chunks into complete W3C SSE events before guardrail or DLP evaluation.
2. CurrencyNormalizer: Integer-only (`divmod`) minor-unit formatter & basis-point
   proration calculator supporting ISO-4217 0-decimal, 2-decimal, and 3-decimal currencies.
3. validate_subscription_transition: Pre-mutation Stripe SubscriptionSchedule guard.
4. commit_before_external_cleanup: Enforces DB persistence before irreversible
   S3/Stripe external deletions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence


class BillingValidationError(ValueError):
    """Raised when a currency, plan, schedule state, or amount fails fail-closed validation."""


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

# ISO-4217 Three-Decimal currencies where 1000 minor units = 1.000 major unit
THREE_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BHD",
        "IQD",
        "JOD",
        "KWD",
        "LYD",
        "OMR",
        "TND",
    }
)


@dataclass(frozen=True)
class FormattedPrice:
    currency: str
    minor_units: int
    exponent: int
    display: str


@dataclass(frozen=True)
class SubscriptionTransitionReport:
    allowed: bool
    requires_schedule_release: bool
    schedule_id: str | None
    from_currency: str
    to_currency: str
    reason: str


class CurrencyNormalizer:
    """
    Fail-closed currency formatter, basis-point fee calculator, and proration engine
    using pure integer `divmod` math (zero floating-point rounding drift).
    """

    def __init__(self, canonical_catalog: Mapping[str, int]) -> None:
        if not canonical_catalog:
            raise BillingValidationError("Canonical price catalog cannot be empty")
        self._catalog: dict[str, int] = {}
        for k, v in canonical_catalog.items():
            if isinstance(v, bool) or not isinstance(v, int):
                raise BillingValidationError(f"Catalog price for {k!r} must be an integer minor unit")
            code = k.strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise BillingValidationError(f"Invalid ISO-4217 currency code: {k!r}")
            if v <= 0:
                raise BillingValidationError(f"Catalog minor-unit price must be positive for {code}")
            self._catalog[code] = v

    @property
    def supported_currencies(self) -> frozenset[str]:
        return frozenset(self._catalog.keys())

    @staticmethod
    def currency_exponent(currency: str) -> int:
        code = (currency or "").strip().upper()
        if code in ZERO_DECIMAL_CURRENCIES:
            return 0
        if code in THREE_DECIMAL_CURRENCIES:
            return 3
        return 2

    def resolve_minor_units(self, currency: str) -> int:
        code = (currency or "").strip().upper()
        if code not in self._catalog:
            raise BillingValidationError(f"Unsupported or unconfigured currency: {currency!r}")
        return self._catalog[code]

    def format_minor_units(self, currency: str, minor_units: int | None = None) -> FormattedPrice:
        code = (currency or "").strip().upper()
        if code not in self._catalog:
            raise BillingValidationError(f"Unsupported or unconfigured currency: {currency!r}")
        if minor_units is None:
            units = self._catalog[code]
        else:
            if isinstance(minor_units, bool) or not isinstance(minor_units, int):
                raise BillingValidationError("Minor units must be an exact integer")
            units = minor_units
        if units < 0:
            raise BillingValidationError("Minor units cannot be negative")

        exp = self.currency_exponent(code)
        if exp == 0:
            display = f"{units:,} {code}"
        elif exp == 3:
            major, rem = divmod(units, 1000)
            display = f"{major:,}.{rem:03d} {code}"
        else:
            major, rem = divmod(units, 100)
            display = f"{major:,}.{rem:02d} {code}"
        return FormattedPrice(currency=code, minor_units=units, exponent=exp, display=display)

    @staticmethod
    def compute_basis_point_fee(minor_units: int, basis_points: int) -> int:
        """
        Computes `minor_units * (basis_points / 10,000)` using exact integer half-up
        arithmetic with zero floating-point drift.
        """
        if isinstance(minor_units, bool) or not isinstance(minor_units, int) or minor_units < 0:
            raise BillingValidationError("minor_units must be a non-negative integer")
        if isinstance(basis_points, bool) or not isinstance(basis_points, int) or not (0 <= basis_points <= 10_000):
            raise BillingValidationError("basis_points must be an integer between 0 and 10000")
        return (minor_units * basis_points + 5_000) // 10_000

    @staticmethod
    def prorate_minor_units(minor_units: int, active_seconds: int, period_seconds: int) -> int:
        """
        Computes exact integer half-up proration (`minor_units * active_seconds / period_seconds`).
        """
        if isinstance(minor_units, bool) or not isinstance(minor_units, int) or minor_units < 0:
            raise BillingValidationError("minor_units must be a non-negative integer")
        if isinstance(period_seconds, bool) or not isinstance(period_seconds, int) or period_seconds <= 0:
            raise BillingValidationError("period_seconds must be a positive integer")
        if (
            isinstance(active_seconds, bool)
            or not isinstance(active_seconds, int)
            or active_seconds < 0
            or active_seconds > period_seconds
        ):
            raise BillingValidationError("active_seconds must be an integer within [0, period_seconds]")
        return (minor_units * active_seconds + (period_seconds // 2)) // period_seconds


def validate_subscription_transition(
    normalizer: CurrencyNormalizer,
    current_currency: str,
    target_currency: str,
    active_schedule_id: str | None = None,
    schedule_released: bool = False,
) -> SubscriptionTransitionReport:
    """
    Pre-mutation guard for Stripe subscription upgrades/downgrades.
    Verifies both currencies exist in the canonical catalog, blocks direct mutations
    when an unreleased SubscriptionSchedule is attached, and rejects cross-currency
    in-place mutations.
    """
    curr_code = (current_currency or "").strip().upper()
    tgt_code = (target_currency or "").strip().upper()
    normalizer.resolve_minor_units(curr_code)
    normalizer.resolve_minor_units(tgt_code)

    clean_sched = (active_schedule_id or "").strip() or None
    if clean_sched and not schedule_released:
        return SubscriptionTransitionReport(
            allowed=False,
            requires_schedule_release=True,
            schedule_id=clean_sched,
            from_currency=curr_code,
            to_currency=tgt_code,
            reason=f"Subscription is controlled by active schedule {clean_sched}; release schedule before mutating",
        )

    if curr_code != tgt_code:
        return SubscriptionTransitionReport(
            allowed=False,
            requires_schedule_release=False,
            schedule_id=clean_sched,
            from_currency=curr_code,
            to_currency=tgt_code,
            reason=f"Cross-currency in-place mutation ({curr_code} -> {tgt_code}) is forbidden; recreate checkout session",
        )

    return SubscriptionTransitionReport(
        allowed=True,
        requires_schedule_release=False,
        schedule_id=clean_sched,
        from_currency=curr_code,
        to_currency=tgt_code,
        reason="Transition verified safe",
    )


@dataclass(frozen=True)
class ParsedSSEEvent:
    event: str
    data: str
    event_id: str | None = None
    retry_ms: int | None = None


@dataclass(frozen=True)
class SSEScanReport:
    frame_count: int
    reconstructed_text: str
    matched_patterns: tuple[str, ...]
    blocked: bool


class SSEFrameAssembler:
    """
    Stateful W3C Server-Sent Events (SSE) stream framing normalizer.

    Buffers arbitrary incoming transport chunks and yields only complete logical
    SSE event blocks (delimited by `\\n\\n`, `\\r\\n\\r\\n`, or `\\r\\r`), preventing
    split-chunk framing bypasses in downstream AI safety guardrails.
    """

    def __init__(self, max_buffer_chars: int = 262_144) -> None:
        self._buffer: str = ""
        self._max_buffer_chars = max_buffer_chars

    def feed(self, chunk: str | bytes) -> list[str]:
        if isinstance(chunk, bytes):
            text = chunk.decode("utf-8", errors="replace")
        else:
            text = str(chunk)

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self._buffer += normalized
        if len(self._buffer) > self._max_buffer_chars:
            raise ValueError("SSE frame buffer exceeded maximum allowed size")

        complete_frames: list[str] = []
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            non_comment_lines = [
                ln for ln in frame.split("\n") if ln.strip() and not ln.lstrip().startswith(":")
            ]
            if non_comment_lines:
                complete_frames.append(frame)
        return complete_frames

    def feed_events(self, chunk: str | bytes) -> list[ParsedSSEEvent]:
        raw_frames = self.feed(chunk)
        return [self.parse_frame(f) for f in raw_frames]

    def flush(self) -> list[str]:
        remaining = self._buffer.strip()
        self._buffer = ""
        if not remaining:
            return []
        non_comment_lines = [
            ln for ln in remaining.split("\n") if ln.strip() and not ln.lstrip().startswith(":")
        ]
        return [remaining] if non_comment_lines else []

    @staticmethod
    def parse_frame(frame: str) -> ParsedSSEEvent:
        event_type = "message"
        data_lines: list[str] = []
        event_id: str | None = None
        retry_ms: int | None = None

        for line in frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            if ":" in line:
                field, val = line.split(":", 1)
                if val.startswith(" "):
                    val = val[1:]
            else:
                field, val = line, ""

            if field == "event" and val:
                event_type = val
            elif field == "data":
                data_lines.append(val)
            elif field == "id":
                event_id = val
            elif field == "retry" and val.isdigit():
                retry_ms = int(val)

        return ParsedSSEEvent(
            event=event_type,
            data="\n".join(data_lines),
            event_id=event_id,
            retry_ms=retry_ms,
        )

    @staticmethod
    def extract_data_payload(frames: Iterable[str]) -> str:
        """Concatenates `data:` payloads across one or more complete SSE frames."""
        parts: list[str] = []
        for frame in frames:
            parsed = SSEFrameAssembler.parse_frame(frame)
            if parsed.data:
                parts.append(parsed.data)
        return "".join(parts)


def scan_sse_stream_for_leaks(
    chunks: Iterable[str | bytes],
    forbidden_patterns: Sequence[str],
) -> SSEScanReport:
    """
    Reassembles arbitrary transport chunks into complete SSE frames and scans both
    extracted `data:` payloads and raw frame metadata (`id:`, `event:`, or unprefixed lines)
    for forbidden tokens/secrets that may have been split across packet or event boundaries.
    """
    assembler = SSEFrameAssembler()
    all_frames: list[str] = []
    for chunk in chunks:
        all_frames.extend(assembler.feed(chunk))
    all_frames.extend(assembler.flush())

    reconstructed_data = SSEFrameAssembler.extract_data_payload(all_frames)
    raw_joined = "\n".join(all_frames)
    reconstructed = reconstructed_data if reconstructed_data else raw_joined
    matched: list[str] = []
    for pat in forbidden_patterns:
        if pat and (pat in reconstructed_data or pat in raw_joined) and pat not in matched:
            matched.append(pat)

    return SSEScanReport(
        frame_count=len(all_frames),
        reconstructed_text=reconstructed,
        matched_patterns=tuple(matched),
        blocked=len(matched) > 0,
    )


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


def allocate_bps_split(total_units: int, weights_bps: Sequence[int]) -> list[int]:
    """
    Allocates total_units across multiple recipients based on basis points (bps)
    weights using the Largest Remainder Method (Hare-Niemeyer method).
    Guarantees zero-remainder loss: sum(result) == total_units in 100% of cases.
    """
    if isinstance(total_units, bool) or not isinstance(total_units, int):
        raise TypeError("total_units must be an integer")
    if total_units < 0:
        raise ValueError("total_units must be a non-negative integer")
    if not weights_bps:
        raise ValueError("weights_bps cannot be empty")
    for w in weights_bps:
        if isinstance(w, bool) or not isinstance(w, int) or w < 0:
            raise ValueError("All weights in weights_bps must be non-negative integers")
    if sum(weights_bps) != 10_000:
        raise ValueError(f"sum(weights_bps) must equal 10,000, got {sum(weights_bps)}")

    shares = [0] * len(weights_bps)
    remainders = []
    for idx, w in enumerate(weights_bps):
        floor_val, rem = divmod(total_units * w, 10_000)
        shares[idx] = floor_val
        remainders.append((rem, idx))

    # Sort descending by remainder, breaking ties stably by smaller index
    remainders.sort(key=lambda item: (-item[0], item[1]))

    surplus = total_units - sum(shares)
    for i in range(surplus):
        target_idx = remainders[i][1]
        shares[target_idx] += 1

    return shares
