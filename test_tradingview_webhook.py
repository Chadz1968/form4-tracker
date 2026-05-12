import json
import os
import tempfile
import unittest

import trade_manager_journal as journal
import tradingview_webhook as webhook


class TradingViewWebhookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_signal_inbox = journal.SIGNAL_INBOX_FILE
        journal.SIGNAL_INBOX_FILE = os.path.join(self.tmp.name, "signal_inbox.json")
        with open(journal.SIGNAL_INBOX_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)

    def tearDown(self):
        journal.SIGNAL_INBOX_FILE = self.original_signal_inbox
        self.tmp.cleanup()

    def test_valid_alert_is_stored(self):
        status, response = webhook.handle_tradingview_alert({
            "symbol": "aapl",
            "timeframe": "5m",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": "190.25",
            "trigger": "close_above_vwap",
            "notes": "High relative volume",
            "alert_id": "a1",
        }, expected_secret=None)

        self.assertEqual(status, 201)
        self.assertTrue(response["ok"])
        self.assertEqual(response["symbol"], "AAPL")

        inbox = journal._load_json(journal.SIGNAL_INBOX_FILE, [])
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["symbol"], "AAPL")
        self.assertEqual(inbox[0]["raw_payload"]["alert_id"], "a1")

    def test_missing_required_field_is_rejected(self):
        status, response = webhook.handle_tradingview_alert({
            "symbol": "AAPL",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 190.25,
            "trigger": "close_above_vwap",
        }, expected_secret=None)

        self.assertEqual(status, 400)
        self.assertFalse(response["ok"])
        self.assertIn("timeframe", response["error"])

    def test_duplicate_alert_id_is_not_stored_twice(self):
        payload = {
            "symbol": "AAPL",
            "timeframe": "5m",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 190.25,
            "trigger": "close_above_vwap",
            "alert_id": "dupe-1",
        }
        first_status, _ = webhook.handle_tradingview_alert(payload, expected_secret=None)
        second_status, second_response = webhook.handle_tradingview_alert(payload, expected_secret=None)

        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 200)
        self.assertEqual(second_response["status"], "duplicate")

        inbox = journal._load_json(journal.SIGNAL_INBOX_FILE, [])
        self.assertEqual(len(inbox), 1)

    def test_secret_is_required_when_configured(self):
        payload = {
            "symbol": "AAPL",
            "timeframe": "5m",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 190.25,
            "trigger": "close_above_vwap",
        }

        bad_status, bad_response = webhook.handle_tradingview_alert(
            payload,
            header_secret="wrong",
            expected_secret="expected",
        )
        good_status, good_response = webhook.handle_tradingview_alert(
            payload,
            header_secret="expected",
            expected_secret="expected",
        )

        self.assertEqual(bad_status, 401)
        self.assertFalse(bad_response["ok"])
        self.assertEqual(good_status, 201)
        self.assertTrue(good_response["ok"])


if __name__ == "__main__":
    unittest.main()
