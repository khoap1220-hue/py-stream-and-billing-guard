import unittest

from stream_billing_guard import (
    BillingValidationError,
    CurrencyNormalizer,
    SSEChunkNormalizer,
    SSEFrameAssembler,
    commit_before_external_cleanup,
    scan_sse_stream_for_leaks,
    validate_subscription_transition,
)


class TestStreamAndBillingGuard(unittest.TestCase):
    def test_zero_two_and_three_decimal_integer_formatting(self) -> None:
        catalog = {"USD": 1999, "EUR": 1805, "JPY": 3000, "VND": 250000, "KWD": 4505}
        normalizer = CurrencyNormalizer(catalog)

        usd = normalizer.format_minor_units("usd")
        self.assertEqual(usd.minor_units, 1999)
        self.assertEqual(usd.exponent, 2)
        self.assertEqual(usd.display, "19.99 USD")

        eur = normalizer.format_minor_units("EUR")
        self.assertEqual(eur.display, "18.05 EUR")

        jpy = normalizer.format_minor_units("jpy")
        self.assertEqual(jpy.minor_units, 3000)
        self.assertEqual(jpy.exponent, 0)
        self.assertEqual(jpy.display, "3,000 JPY")

        kwd = normalizer.format_minor_units("kwd")
        self.assertEqual(kwd.minor_units, 4505)
        self.assertEqual(kwd.exponent, 3)
        self.assertEqual(kwd.display, "4.505 KWD")

    def test_basis_point_fee_and_integer_proration_have_zero_float_drift(self) -> None:
        # 1999 cents * 290 bps (2.90%) = 57.971 -> 58 cents half-up
        self.assertEqual(CurrencyNormalizer.compute_basis_point_fee(1999, 290), 58)
        # Half-cycle proration of 3000 JPY over 15/30 days = 1500 JPY
        self.assertEqual(CurrencyNormalizer.prorate_minor_units(3000, 15, 30), 1500)
        with self.assertRaises(BillingValidationError):
            CurrencyNormalizer.prorate_minor_units(100.5, 15, 30)  # type: ignore[arg-type]
        with self.assertRaises(BillingValidationError):
            CurrencyNormalizer.prorate_minor_units(True, 1, 1)

    def test_fail_closed_on_unconfigured_or_invalid_currency(self) -> None:
        normalizer = CurrencyNormalizer({"USD": 1000})
        with self.assertRaises(BillingValidationError):
            normalizer.resolve_minor_units("CHF")
        with self.assertRaises(BillingValidationError):
            CurrencyNormalizer({"USD": 0})

    def test_validate_subscription_transition_enforces_schedule_release_and_currency_match(self) -> None:
        normalizer = CurrencyNormalizer({"USD": 1999, "JPY": 3000})

        blocked_sched = validate_subscription_transition(
            normalizer,
            current_currency="USD",
            target_currency="USD",
            active_schedule_id="sub_sched_123",
            schedule_released=False,
        )
        self.assertFalse(blocked_sched.allowed)
        self.assertTrue(blocked_sched.requires_schedule_release)

        blocked_cross_currency = validate_subscription_transition(
            normalizer,
            current_currency="USD",
            target_currency="JPY",
            active_schedule_id="sub_sched_123",
            schedule_released=True,
        )
        self.assertFalse(blocked_cross_currency.allowed)

        ok_transition = validate_subscription_transition(
            normalizer,
            current_currency="USD",
            target_currency="USD",
            active_schedule_id="sub_sched_123",
            schedule_released=True,
        )
        self.assertTrue(ok_transition.allowed)

    def test_sse_frame_assembler_and_leak_scanner_catch_split_chunk_bypasses(self) -> None:
        chunks = [
            b": keepalive\n\nevent: content_block_delta\nid: msg_1\ndata: Bearer sk_live_",
            b"9988776655\n\nevent: message_stop\ndata: [DONE]\n\n",
        ]
        rep = scan_sse_stream_for_leaks(chunks, ["sk_live_9988776655", "PRIVATE_KEY"])
        self.assertTrue(rep.blocked)
        self.assertEqual(rep.frame_count, 2)
        self.assertEqual(rep.matched_patterns, ("sk_live_9988776655",))

        id_leak = scan_sse_stream_for_leaks([b"id: sk_live_id_secret\nevent: ping\n\n"], ["sk_live_id_secret"])
        self.assertTrue(id_leak.blocked)

        assembler = SSEFrameAssembler()
        events = assembler.feed_events(chunks[0] + chunks[1])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event, "content_block_delta")
        self.assertEqual(events[0].event_id, "msg_1")
        self.assertEqual(events[0].data, "Bearer sk_live_9988776655")

    def test_commit_before_external_cleanup_guards_side_effects(self) -> None:
        side_effects: list[str] = []

        ok_fail = commit_before_external_cleanup(
            save_fn=lambda: False,
            external_cleanup_fn=lambda: side_effects.append("deleted_s3_object"),
        )
        self.assertFalse(ok_fail)
        self.assertEqual(side_effects, [])

        ok_pass = commit_before_external_cleanup(
            save_fn=lambda: True,
            external_cleanup_fn=lambda: side_effects.append("deleted_s3_object"),
        )
        self.assertTrue(ok_pass)
        self.assertEqual(side_effects, ["deleted_s3_object"])



    def test_stellar_horizon_sse_cursor_resumption_and_heartbeat_filter(self) -> None:
        normalizer = SSEChunkNormalizer(skip_control_events=True)
        self.assertIsNone(normalizer.last_event_id)

        # Chunk 1: event: open handshake with id token
        chunk1 = b": hello\nevent: open\nid: 1000\ndata: {}\n\n"
        frames1 = normalizer.feed(chunk1)
        # Control event filtered out
        self.assertEqual(frames1, [])
        # last_event_id recorded
        self.assertEqual(normalizer.last_event_id, "1000")

        # Chunk 2 & 3: streaming operations across chunks with paging token id: 4294967296
        chunk2 = b"event: message\nid: 4294967296\ndata: {\"id\": \"op_1\", "
        chunk3 = b"\"type\": \"payment\"}\n\n"
        frames2 = normalizer.feed(chunk2)
        self.assertEqual(frames2, [])
        frames3 = normalizer.feed(chunk3)
        self.assertEqual(len(frames3), 1)
        self.assertEqual(normalizer.last_event_id, "4294967296")
        ev = normalizer.parse_frame(frames3[0])
        self.assertEqual(ev.event, "message")
        self.assertEqual(ev.event_id, "4294967296")
        self.assertIn("payment", ev.data)

        # Chunk 4: event: byebye stream termination notice
        chunk4 = b"event: byebye\nid: 4294967297\ndata: byebye\n\n"
        frames4 = normalizer.feed(chunk4)
        self.assertEqual(frames4, [])
        self.assertEqual(normalizer.last_event_id, "4294967297")

        # When skip_control_events is False, control events are yielded
        verbose_normalizer = SSEChunkNormalizer(skip_control_events=False)
        f_open = verbose_normalizer.feed(chunk1)
        self.assertEqual(len(f_open), 1)
        self.assertEqual(verbose_normalizer.last_event_id, "1000")

if __name__ == "__main__":
    unittest.main()
