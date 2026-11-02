import unittest

from stream_billing_guard import (
    BillingValidationError,
    CurrencyNormalizer,
    SSEFrameAssembler,
    commit_before_external_cleanup,
)


class TestStreamAndBillingGuard(unittest.TestCase):
    def test_zero_decimal_jpy_vs_two_decimal_usd_formatting(self) -> None:
        catalog = {"USD": 1999, "EUR": 1899, "JPY": 3000}
        normalizer = CurrencyNormalizer(catalog)

        usd = normalizer.format_minor_units("usd")
        self.assertEqual(usd.minor_units, 1999)
        self.assertEqual(usd.display, "19.99 USD")

        jpy = normalizer.format_minor_units("jpy")
        self.assertEqual(jpy.minor_units, 3000)
        self.assertEqual(jpy.display, "3,000 JPY")

    def test_fail_closed_on_unconfigured_currency(self) -> None:
        normalizer = CurrencyNormalizer({"USD": 1000})
        with self.assertRaises(BillingValidationError):
            normalizer.resolve_minor_units("CHF")

    def test_sse_frame_assembler_prevents_split_chunk_bypass(self) -> None:
        assembler = SSEFrameAssembler()
        # Simulate a sensitive token split across two TCP transport chunks
        chunk_1 = b"event: content_block_delta\ndata: UNSAFE_"
        chunk_2 = b"TOKEN_PAYLOAD\n\n"

        frames_1 = assembler.feed(chunk_1)
        self.assertEqual(frames_1, [], "Partial frame must be buffered until delimiter arrives")

        frames_2 = assembler.feed(chunk_2)
        self.assertEqual(len(frames_2), 1)
        reconstructed = SSEFrameAssembler.extract_data_payload(frames_2)
        self.assertEqual(reconstructed, "UNSAFE_TOKEN_PAYLOAD")

    def test_commit_before_external_cleanup_guards_side_effects(self) -> None:
        side_effects: list[str] = []

        # Negative boundary: DB save fails -> zero external side effects
        ok_fail = commit_before_external_cleanup(
            save_fn=lambda: False,
            external_cleanup_fn=lambda: side_effects.append("deleted_s3_object"),
        )
        self.assertFalse(ok_fail)
        self.assertEqual(side_effects, [])

        # Positive path: DB save succeeds -> external cleanup executes once
        ok_pass = commit_before_external_cleanup(
            save_fn=lambda: True,
            external_cleanup_fn=lambda: side_effects.append("deleted_s3_object"),
        )
        self.assertTrue(ok_pass)
        self.assertEqual(side_effects, ["deleted_s3_object"])


if __name__ == "__main__":
    unittest.main()
