from __future__ import annotations

import json
import sys
from typing import Any

from .core import (
    CurrencyNormalizer,
    scan_sse_stream_for_leaks,
    validate_subscription_transition,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(
            "Usage:\n"
            "  python -m stream_billing_guard audit-catalog '<json-catalog>'\n"
            "  python -m stream_billing_guard prorate <currency> <minor_units> <active_sec> <period_sec> [fee_bps]\n"
            "  python -m stream_billing_guard scan-sse '<chunks-json>' '<patterns-json>'\n"
        )
        return 1

    cmd = args[0]
    if cmd == "audit-catalog":
        raw_catalog: dict[str, int] = json.loads(args[1]) if len(args) > 1 else {"USD": 1999, "JPY": 3000, "KWD": 5500}
        normalizer = CurrencyNormalizer(raw_catalog)
        formatted = []
        for code in sorted(normalizer.supported_currencies):
            fp = normalizer.format_minor_units(code)
            formatted.append(
                {
                    "currency": fp.currency,
                    "minor_units": fp.minor_units,
                    "exponent": fp.exponent,
                    "display": fp.display,
                    "platform_fee_250bps": normalizer.compute_basis_point_fee(fp.minor_units, 250),
                }
            )
        transition = validate_subscription_transition(
            normalizer,
            current_currency=formatted[0]["currency"],
            target_currency=formatted[0]["currency"],
            active_schedule_id="sub_sched_live_01",
            schedule_released=False,
        )
        out: dict[str, Any] = {
            "catalog_count": len(formatted),
            "formatted_prices": formatted,
            "unreleased_schedule_guard_blocked": not transition.allowed,
            "schedule_guard_reason": transition.reason,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if cmd == "prorate":
        currency = args[1]
        minor_units = int(args[2])
        active_sec = int(args[3])
        period_sec = int(args[4])
        fee_bps = int(args[5]) if len(args) > 5 else 290
        normalizer = CurrencyNormalizer({currency: max(1, minor_units)})
        prorated = normalizer.prorate_minor_units(minor_units, active_sec, period_sec)
        fee = normalizer.compute_basis_point_fee(prorated, fee_bps)
        fp = normalizer.format_minor_units(currency, prorated)
        print(
            json.dumps(
                {
                    "currency": fp.currency,
                    "original_minor_units": minor_units,
                    "prorated_minor_units": prorated,
                    "prorated_display": fp.display,
                    "fee_bps": fee_bps,
                    "fee_minor_units": fee,
                    "net_minor_units": prorated - fee,
                },
                indent=2,
            )
        )
        return 0

    if cmd == "scan-sse":
        chunks: list[str] = json.loads(args[1])
        patterns: list[str] = json.loads(args[2])
        rep = scan_sse_stream_for_leaks(chunks, patterns)
        print(
            json.dumps(
                {
                    "frame_count": rep.frame_count,
                    "reconstructed_text": rep.reconstructed_text,
                    "matched_patterns": list(rep.matched_patterns),
                    "blocked": rep.blocked,
                },
                indent=2,
            )
        )
        return 2 if rep.blocked else 0

    sys.stderr.write(f"Unknown command: {cmd}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
