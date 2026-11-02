# 🐍 py-stream-and-billing-guard (v0.3.0)

[![Tests](https://img.shields.io/badge/Tests-6%2F6_Passing-2ea44f?logo=python)](tests/test_guard.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![GPG Verified](https://img.shields.io/badge/Commits-GPG_Verified-2ea44f?logo=github)](https://github.com/khoap1220-hue/py-stream-and-billing-guard/commits/main)

**`py-stream-and-billing-guard`** provides zero-dependency, stdlib-only defensive engineering primitives + CLI tools for **Python / FastAPI backends, AI streaming proxies, and multi-currency Stripe billing services**.

> 🔗 **Production Heritage:** Built from real-world production patterns including our upstream-credited multi-currency Stripe billing work on **[BasedHardware/omi#23591](https://github.com/BasedHardware/omi/pull/23591)** and defensive SSE streaming guardrail architecture.

---

## ✨ Key Modules

1. **`SSEFrameAssembler` & `scan_sse_stream_for_leaks` (W3C Cross-Chunk Stream Framing Normalizer):** Buffers partial HTTP/TCP chunks (`\n\n`, `\r\n\r\n`), parses structured `event:`, `id:`, `retry:`, and `data:` fields, and scans reassembled streams so AI safety/DLP guardrails never miss sensitive tokens split across packet boundaries.
2. **`CurrencyNormalizer` (Zero-Float-Drift Integer Billing & Proration Engine):** Uses pure integer `divmod` arithmetic across ISO-4217 **Zero-Decimal (`JPY`, `KRW`, `VND`)**, **Two-Decimal (`USD`, `EUR`)**, and **Three-Decimal (`KWD`, `BHD`, `JOD`)** currencies, plus exact integer basis-point (`1/10,000`) fee and mid-cycle proration math.
3. **`validate_subscription_transition` & `commit_before_external_cleanup`:** Blocks in-place Stripe subscription mutations when an unreleased `SubscriptionSchedule` is attached, and guarantees database commits succeed before executing irreversible S3/Stripe deletions.

---

## 🚀 Library & CLI Quickstart

```python
from stream_billing_guard import (
    CurrencyNormalizer,
    scan_sse_stream_for_leaks,
    validate_subscription_transition,
)

normalizer = CurrencyNormalizer({"USD": 1999, "JPY": 3000, "KWD": 4505})
print(normalizer.format_minor_units("JPY").display)  # "3,000 JPY"
print(normalizer.format_minor_units("KWD").display)  # "4.505 KWD"

report = scan_sse_stream_for_leaks(
    [b"event: delta\ndata: sk_live_", b"998877\n\n"],
    forbidden_patterns=["sk_live_998877"],
)
assert report.blocked is True
```

### CLI Usage

```bash
python -m stream_billing_guard audit-catalog '{"USD":1999,"JPY":3000,"VND":250000,"KWD":4505}'
python -m stream_billing_guard prorate USD 1999 15 30 290
```

## 🧪 Running Tests

```bash
python -m unittest discover -s tests -v
```

## 📄 License

Released under the [MIT License](LICENSE).
