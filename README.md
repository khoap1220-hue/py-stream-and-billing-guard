# 🐍 py-stream-and-billing-guard

[![Tests](https://img.shields.io/badge/Tests-4%2F4_Passing-2ea44f?logo=python)](tests/test_guard.py)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![GPG Verified](https://img.shields.io/badge/Commits-GPG_Verified-2ea44f?logo=github)](https://github.com/khoap1220-hue/py-stream-and-billing-guard/commits/main)

**`py-stream-and-billing-guard`** provides lightweight, stdlib-only defensive engineering primitives for **Python / FastAPI backends, AI streaming proxies, and multi-currency Stripe billing services**.

> 🔗 **Production Heritage:** Built from real-world production patterns including our upstream-credited multi-currency Stripe billing work on **[BasedHardware/omi#23591](https://github.com/BasedHardware/omi/pull/23591)** and defensive SSE streaming guardrail architecture.

---

## ✨ Key Modules

1. **`SSEFrameAssembler` (Cross-Chunk Stream Framing Normalizer):** Buffers partial HTTP/TCP chunks until a complete `

` SSE event boundary is reached, ensuring AI content guardrails never miss tokens split across transport packets.
2. **`CurrencyNormalizer` (Zero-Decimal & Basis-Point Billing Guard):** Dynamically derives supported currencies from a canonical catalog, formats ISO-4217 Zero-Decimal currencies (`JPY`, `KRW`, `VND`) without `÷ 100` truncation errors, and fails closed on unknown currency codes.
3. **`commit_before_external_cleanup` (Transactional Side-Effect Guard):** Guarantees database commits succeed before executing irreversible S3 asset deletions or external Stripe schedule releases.

---

## 🚀 Quickstart

```python
from stream_billing_guard import CurrencyNormalizer, SSEFrameAssembler

normalizer = CurrencyNormalizer({"USD": 1999, "JPY": 3000})
print(normalizer.format_minor_units("JPY").display)  # "3,000 JPY"

assembler = SSEFrameAssembler()
assert assembler.feed(b"data: hel") == []
frames = assembler.feed(b"lo world

")
print(SSEFrameAssembler.extract_data_payload(frames))  # "hello world"
```

## 🧪 Running Tests

```bash
python -m unittest discover -s tests -v
```

## 📄 License

Released under the [MIT License](LICENSE).
