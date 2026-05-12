import json
import os
import tempfile
import unittest

import trade_manager_journal as journal
import trade_manager_ui as ui


class TradeManagerUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_journal_paths = {
            "TRADING_POLICY_FILE": journal.TRADING_POLICY_FILE,
            "RISK_RULES_FILE": journal.RISK_RULES_FILE,
            "PLAYBOOK_FILE": journal.PLAYBOOK_FILE,
            "SIGNAL_INBOX_FILE": journal.SIGNAL_INBOX_FILE,
            "TRADE_JOURNAL_FILE": journal.TRADE_JOURNAL_FILE,
            "DAILY_REVIEWS_FILE": journal.DAILY_REVIEWS_FILE,
        }
        self.original_account_snapshot = ui.ALPACA_ACCOUNT_SNAPSHOT_FILE
        for name in self.original_journal_paths:
            setattr(journal, name, os.path.join(self.tmp.name, os.path.basename(getattr(journal, name))))
        ui.ALPACA_ACCOUNT_SNAPSHOT_FILE = os.path.join(self.tmp.name, "alpaca_account_snapshot.json")

        self._write(journal.TRADING_POLICY_FILE, {
            "mode": "paper",
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
            "approved_setups": [{"id": "vwap_reclaim", "name": "VWAP Reclaim"}]
        })
        self._write(journal.SIGNAL_INBOX_FILE, [])
        self._write(journal.TRADE_JOURNAL_FILE, [])
        self._write(journal.DAILY_REVIEWS_FILE, [])
        self._write(ui.ALPACA_ACCOUNT_SNAPSHOT_FILE, {"equity": 10000.0})

        self.signal = journal.ingest_signal({
            "source": "tradingview",
            "symbol": "AAPL",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 100.0,
            "timeframe": "5m",
            "trigger": "close_above_vwap",
        })

    def tearDown(self):
        for name, path in self.original_journal_paths.items():
            setattr(journal, name, path)
        ui.ALPACA_ACCOUNT_SNAPSHOT_FILE = self.original_account_snapshot
        self.tmp.cleanup()

    def _write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_load_ui_state_includes_signal_and_equity(self):
        status, data = ui.handle_api("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["signals"]), 1)
        self.assertEqual(data["account_equity"], 10000.0)

    def test_coach_check_uses_account_snapshot_when_equity_missing(self):
        status, data = ui.handle_api("POST", "/api/coach-check", {
            "signal_id": self.signal["id"],
            "entry_price": 100,
            "stop_price": 99,
            "target_price": 102.5,
            "setup_grade": "A",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["decision"]["approved"])
        self.assertEqual(data["decision"]["sized_risk_dollars"], 25.0)
        self.assertEqual(data["plan"]["reward_to_risk"], 2.5)

    def test_save_plan_journals_trade_and_updates_signal_status(self):
        status, data = ui.handle_api("POST", "/api/save-plan", {
            "signal_id": self.signal["id"],
            "entry_price": 100,
            "stop_price": 99,
            "target_price": 102.5,
            "setup_grade": "A",
            "notes": "Clean reclaim",
        })
        self.assertEqual(status, 201)
        self.assertEqual(data["journal_entry"]["status"], "planned")

        state = ui.load_ui_state()
        self.assertEqual(len(state["trades"]), 1)
        self.assertEqual(state["signals"][0]["status"], "planned")

    def test_signal_status_endpoint_can_expire_signal(self):
        status, data = ui.handle_api("POST", "/api/signal-status", {
            "signal_id": self.signal["id"],
            "status": "expired",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(ui.load_ui_state()["signals"][0]["status"], "expired")


if __name__ == "__main__":
    unittest.main()
