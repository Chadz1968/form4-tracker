import json
import os
import tempfile
import unittest

import trade_manager_journal as journal


class TradeManagerJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_paths = {
            "TRADING_POLICY_FILE": journal.TRADING_POLICY_FILE,
            "RISK_RULES_FILE": journal.RISK_RULES_FILE,
            "PLAYBOOK_FILE": journal.PLAYBOOK_FILE,
            "SIGNAL_INBOX_FILE": journal.SIGNAL_INBOX_FILE,
            "TRADE_JOURNAL_FILE": journal.TRADE_JOURNAL_FILE,
            "DAILY_REVIEWS_FILE": journal.DAILY_REVIEWS_FILE,
        }
        for name in self.original_paths:
            setattr(journal, name, os.path.join(self.tmp.name, os.path.basename(getattr(journal, name))))

        self._write(journal.TRADING_POLICY_FILE, {
            "coach_mode": True,
            "allow_live_trading": False,
            "require_setup_tag": True,
            "require_stop_before_entry": True,
            "require_target_before_entry": True,
        })
        self._write(journal.RISK_RULES_FILE, {
            "risk_per_trade_pct": 0.0025,
            "minimum_reward_to_risk": 2.0,
            "max_trades_per_day": 3,
            "max_consecutive_losses": 2,
            "block_after_max_trades": True,
            "block_after_loss_streak": True,
        })
        self._write(journal.PLAYBOOK_FILE, {
            "approved_setups": [
                {"id": "vwap_reclaim", "name": "VWAP Reclaim"}
            ]
        })
        self._write(journal.SIGNAL_INBOX_FILE, [])
        self._write(journal.TRADE_JOURNAL_FILE, [])
        self._write(journal.DAILY_REVIEWS_FILE, [])

    def tearDown(self):
        for name, path in self.original_paths.items():
            setattr(journal, name, path)
        self.tmp.cleanup()

    def _write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_foundation_files_validate(self):
        health = journal.validate_foundation_files()
        self.assertTrue(health["ok"])
        self.assertEqual(health["approved_setups"], ["vwap_reclaim"])

    def test_signal_to_plan_to_exit_lifecycle(self):
        signal = journal.ingest_signal({
            "source": "tradingview",
            "symbol": "AAPL",
            "timeframe": "5m",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 100.0,
            "trigger": "close_above_vwap",
        })
        plan = journal.create_trade_plan(
            signal,
            entry_price=100.0,
            stop_price=99.0,
            target_price=102.5,
            account_equity=10000.0,
            setup_grade="A",
        )
        decision = journal.evaluate_trade_plan(plan, today_trades=[])
        self.assertTrue(decision["approved"])
        self.assertEqual(decision["sized_risk_dollars"], 25.0)

        entry = journal.journal_trade_plan(plan, decision)
        opened = journal.record_trade_open(entry["id"], broker_order_id="paper-1")
        self.assertEqual(opened["status"], "open")

        closed = journal.record_trade_exit(entry["id"], exit_price=102.5, exit_reason="target")
        self.assertEqual(closed["outcome"], "win")
        self.assertEqual(closed["r_multiple"], 2.5)

        review = journal.build_daily_review(closed["date"])
        self.assertEqual(review["closed"], 1)
        self.assertEqual(review["total_r"], 2.5)

    def test_rejects_unapproved_setup(self):
        signal = journal.ingest_signal({
            "source": "tradingview",
            "symbol": "TSLA",
            "setup": "random_chase",
            "side": "long",
            "price": 200.0,
        })
        plan = journal.create_trade_plan(
            signal,
            entry_price=200.0,
            stop_price=199.0,
            target_price=203.0,
        )
        decision = journal.evaluate_trade_plan(plan, today_trades=[])
        self.assertFalse(decision["approved"])
        self.assertIn("Setup is not approved", decision["reasons"][0])


if __name__ == "__main__":
    unittest.main()
