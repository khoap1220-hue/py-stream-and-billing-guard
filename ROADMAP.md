# Roadmap — `py-stream-and-billing-guard`

- [x] W3C Server-Sent Events (`SSEFrameAssembler`) cross-chunk boundary reassembly.
- [x] Split-packet secret and JWT leak scanner (`scan_sse_stream_for_leaks`).
- [x] Integer-only `divmod` ISO-4217 `0/2/3-decimal` (`JPY`, `USD`, `KWD`) currency normalizer.
- [x] Exact basis-point fee & time-proration integer allocator (`allocate_prorated_minor_units`).
- [ ] Optional 7-decimal Stellar `stroops` (`1 XLM = 10^7 stroops`) settlement helper.
