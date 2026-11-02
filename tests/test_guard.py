import unittest

from stream_billing_guard import (
    BillingValidationError,
    CurrencyNormalizer,
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


if __name__ == "__main__":
    unittest.main()
