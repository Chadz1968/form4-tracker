import json
import os
import tempfile
import unittest

import alpaca_reconciliation as recon
import trade_manager_journal as journal


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeAlpacaClient:
    def __init__(self, orders):
        self.orders = orders

    def get_account(self):
        return Obj(
            id="acct-1",
            status="ACTIVE",
            currency="USD",
            cash="9900",
            equity="10100",
            last_equity="10000",
            buying_power="20000",
            daytrade_count=1,
            pattern_day_trader=False,
        )

    def get_all_positions(self):
        return [
            Obj(
                asset_id="asset-aapl",
                symbol="AAPL",
                side="long",
                qty="10",
                avg_entry_price="100",
                market_value="1025",
                cost_basis="1000",
                unrealized_pl="25",
                unrealized_plpc="0.025",
                current_price="102.5",
            )
        ]

    def get_orders(self, filter=None):
        return self.orders


class AlpacaReconciliationTests(unittest.TestCase):
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
        self.original_recon_paths = {
            "ALPACA_ACCOUNT_SNAPSHOT_FILE": recon.ALPACA_ACCOUNT_SNAPSHOT_FILE,
            "ALPACA_POSITIONS_SNAPSHOT_FILE": recon.ALPACA_POSITIONS_SNAPSHOT_FILE,
            "ALPACA_ORDERS_SNAPSHOT_FILE": recon.ALPACA_ORDERS_SNAPSHOT_FILE,
            "ALPACA_RECONCILIATION_LOG_FILE": recon.ALPACA_RECONCILIATION_LOG_FILE,
        }

        for name in self.original_journal_paths:
            setattr(journal, name, os.path.join(self.tmp.name, os.path.basename(getattr(journal, name))))
        for name in self.original_recon_paths:
            setattr(recon, name, os.path.join(self.tmp.name, os.path.basename(getattr(recon, name))))

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
            "approved_setups": [{"id": "vwap_reclaim", "name": "VWAP Reclaim"}]
        })
        self._write(journal.SIGNAL_INBOX_FILE, [])
        self._write(journal.TRADE_JOURNAL_FILE, [])
        self._write(journal.DAILY_REVIEWS_FILE, [])

    def tearDown(self):
        for name, path in self.original_journal_paths.items():
            setattr(journal, name, path)
        for name, path in self.original_recon_paths.items():
            setattr(recon, name, path)
        self.tmp.cleanup()

    def _write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _create_planned_journal_entry(self, broker_order_id="order-1"):
        signal = journal.ingest_signal({
            "source": "tradingview",
            "symbol": "AAPL",
            "setup": "vwap_reclaim",
            "side": "long",
            "price": 100.0,
        })
        plan = journal.create_trade_plan(
            signal,
            entry_price=100.0,
            stop_price=99.0,
            target_price=102.5,
            account_equity=10000.0,
        )
        decision = journal.evaluate_trade_plan(plan, today_trades=[])
        entry = journal.journal_trade_plan(plan, decision)

        entries = journal._load_json(journal.TRADE_JOURNAL_FILE, [])
        entries[0]["broker_order_id"] = broker_order_id
        journal._save_json(journal.TRADE_JOURNAL_FILE, entries)
        return entry

    def test_reconcile_opens_planned_trade_from_parent_fill(self):
        self._create_planned_journal_entry()
        parent = Obj(
            id="order-1",
            client_order_id="client-1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            order_class="bracket",
            status="filled",
            qty="10",
            filled_qty="10",
            filled_avg_price="100",
            filled_at="2026-05-12T13:31:00Z",
            submitted_at="2026-05-12T13:30:00Z",
            legs=[],
        )

        report = recon.reconcile_from_alpaca(FakeAlpacaClient([parent]))
        self.assertEqual(len(report["opened"]), 1)
        self.assertEqual(report["positions"], 1)

        entries = journal._load_json(journal.TRADE_JOURNAL_FILE, [])
        self.assertEqual(entries[0]["status"], "open")
        self.assertEqual(entries[0]["fills"][0]["role"], "entry")

    def test_reconcile_closes_open_trade_from_bracket_leg(self):
        entry = self._create_planned_journal_entry()
        journal.record_trade_open(entry["id"], broker_order_id="order-1", fills=[{
            "broker": "alpaca",
            "order_id": "order-1",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "price": 100,
            "role": "entry",
        }])

        exit_leg = Obj(
            id="leg-target",
            symbol="AAPL",
            side="sell",
            order_type="limit",
            status="filled",
            qty="10",
            filled_qty="10",
            filled_avg_price="102.5",
            filled_at="2026-05-12T14:00:00Z",
        )
        parent = Obj(
            id="order-1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            order_class="bracket",
            status="filled",
            qty="10",
            filled_qty="10",
            filled_avg_price="100",
            filled_at="2026-05-12T13:31:00Z",
            legs=[exit_leg],
        )

        report = recon.reconcile_from_alpaca(FakeAlpacaClient([parent]))
        self.assertEqual(len(report["closed"]), 1)

        entries = journal._load_json(journal.TRADE_JOURNAL_FILE, [])
        self.assertEqual(entries[0]["status"], "closed")
        self.assertEqual(entries[0]["outcome"], "win")
        self.assertEqual(entries[0]["r_multiple"], 2.5)
        self.assertEqual(entries[0]["pnl"], 25.0)
        self.assertEqual(entries[0]["exit"]["reason"], "target")

    def test_snapshots_are_written(self):
        parent = Obj(
            id="order-1",
            symbol="AAPL",
            side="buy",
            order_type="market",
            order_class="bracket",
            status="new",
            qty="10",
            filled_qty="0",
            filled_avg_price=None,
            legs=[],
        )
        recon.reconcile_from_alpaca(FakeAlpacaClient([parent]))
        self.assertTrue(os.path.exists(recon.ALPACA_ACCOUNT_SNAPSHOT_FILE))
        self.assertTrue(os.path.exists(recon.ALPACA_POSITIONS_SNAPSHOT_FILE))
        self.assertTrue(os.path.exists(recon.ALPACA_ORDERS_SNAPSHOT_FILE))
        self.assertTrue(os.path.exists(recon.ALPACA_RECONCILIATION_LOG_FILE))


if __name__ == "__main__":
    unittest.main()
