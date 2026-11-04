"""Offline tests: python3 -m unittest discover -s tests -v"""
import datetime
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nse import backtest as bt, costs, paper, risk, strategies  # noqa: E402
from nse.market import Bar, IST, full_sessions, round_tick  # noqa: E402

CFG = dict(risk.DEFAULTS)


def day_bars(day, path, start_price=1000.0, vol=1000):
    """75 five-minute bars from a list of closes (padded with the last value)."""
    path = (list(path) + [path[-1]] * 75)[:75]
    out, prev = [], start_price
    t = datetime.datetime.combine(day, datetime.time(9, 15), IST)
    for c in path:
        o = prev
        out.append(Bar(t, o, max(o, c) + 0.5, min(o, c) - 0.5, c, vol))
        prev, t = c, t + datetime.timedelta(minutes=5)
    return out


def random_days(n, seed=7, sym_price=1000.0):
    rnd = random.Random(seed)
    bars, p, d = [], sym_price, datetime.date(2026, 1, 5)
    while n:
        if d.weekday() < 5:
            path = []
            p *= 1 + rnd.gauss(0, 0.012)  # overnight gap
            start = p
            for _ in range(75):
                p *= 1 + rnd.gauss(0, 0.002)
                path.append(round(p, 2))
            bars += day_bars(d, path, start)
            n -= 1
        d += datetime.timedelta(days=1)
    return bars


class Costs(unittest.TestCase):
    def test_round_trip_charges_one_lakh(self):
        # Rs 1 lakh buy and sell, intraday: brokerage 20+20, STT 25, exch 5.94, SEBI 0.2, stamp 3, GST 18%
        ch = costs.charges(100000, 100000)
        exp = 40 + 25 + 5.94 + 0.2 + 3 + 0.18 * (40 + 5.94 + 0.2)
        self.assertAlmostEqual(ch, round(exp, 2), places=2)

    def test_small_order_brokerage_is_pct(self):
        self.assertAlmostEqual(costs.charges(10000, 0, {"slippage_pct": 0}),
                               round(3 + 0.297 + 0.01 + 0.3 + 0.18 * (3 + 0.297 + 0.01), 2), places=2)

    def test_slippage_always_hurts(self):
        net, gross, _ = costs.round_trip(+1, 100, 100, 100)
        self.assertLess(gross, 0)
        net, gross, _ = costs.round_trip(-1, 100, 100, 100)
        self.assertLess(gross, 0)


class Exits(unittest.TestCase):
    t = datetime.datetime(2026, 11, 4, 11, 0, tzinfo=IST)

    def test_stop_wins_when_bar_hits_both(self):
        s = strategies.Signal(+1, 100, 99, 101, "")
        hit = bt.exit_check(s, Bar(self.t, 100, 101.5, 98.5, 100, 1), 5, datetime.time(15, 15))
        self.assertEqual(hit, (99, "stop"))

    def test_gap_through_stop_fills_at_open(self):
        s = strategies.Signal(-1, 100, 101, 98, "")
        hit = bt.exit_check(s, Bar(self.t, 102, 103, 101.5, 102, 1), 5, datetime.time(15, 15))
        self.assertEqual(hit, (102, "stop (gap)"))

    def test_square_off(self):
        s = strategies.Signal(+1, 100, 90, 110, "")
        t = datetime.datetime(2026, 11, 4, 15, 10, tzinfo=IST)
        self.assertEqual(bt.exit_check(s, Bar(t, 100, 101, 99, 100.4, 1), 5, datetime.time(15, 15)),
                         (100.4, "square-off"))


class Strategies(unittest.TestCase):
    def test_orb_triggers_on_first_close_above_range(self):
        d = datetime.date(2026, 11, 4)
        bars = day_bars(d, [1001, 1002, 1000, 1003.5, 1005])  # range from first 3 bars
        scan = strategies.get("orb15")
        ctx = {"interval": 5, "prev_close": 1000}
        self.assertIsNone(scan(bars[:3], ctx))
        sig = scan(bars[:4], ctx)
        self.assertEqual(sig.side, 1)
        self.assertEqual(sig.entry, 1003.5)
        self.assertIsNone(scan(bars[:5], ctx), "only the first breakout counts")

    def test_backtest_never_shows_a_strategy_future_bars(self):
        """run_session must only ever hand the strategy bars up to the one being decided on."""
        bars = random_days(10, seed=3)
        sessions = full_sessions(bars, 5)
        for name in strategies.REGISTRY:
            if 5 not in strategies.REGISTRY[name][1]:
                continue
            real, seen = strategies.get(name), []

            def spy(bs, ctx, real=real):
                seen.append(bs[-1].t)
                return real(bs, ctx)
            for k in range(1, len(sessions)):
                seen.clear()
                s = sessions[k][1]
                bt.run_session(spy, s, {"interval": 5, "prev_close": sessions[k - 1][1][-1].c}, CFG)
                self.assertEqual(seen, [b.t for b in s[:len(seen)]], name)  # one new bar per call, in order

    def test_tick_rounding(self):
        self.assertEqual(round_tick(100.03), 100.05)
        self.assertEqual(round_tick(100.03, up=False), 100.0)
        self.assertEqual(round_tick(100.01, up=True), 100.05)


class Risk(unittest.TestCase):
    def test_size_by_risk_capped_by_capital(self):
        self.assertEqual(risk.size(1000, 990, CFG), 50)        # Rs 500 / Rs 10
        self.assertEqual(risk.size(1000, 999.9, CFG), 100)     # capped: Rs 1 lakh / 1000

    def test_entry_window_and_limits(self):
        ok = lambda t, n=0, p=0: risk.can_enter(datetime.time(*t), 1, n, p, CFG)[0]
        self.assertFalse(ok((9, 25)))
        self.assertTrue(ok((10, 0)))
        self.assertFalse(ok((14, 45)))
        self.assertFalse(ok((10, 0), n=3))
        self.assertFalse(ok((10, 0), p=-1500))


class PaperMatchesBacktest(unittest.TestCase):
    def test_replay_equals_backtest(self):
        cfg = dict(CFG, strategies=["orb15"], max_trades_per_day=50)
        data = {"AAA": random_days(8, seed=1), "BBB": random_days(8, seed=2, sym_price=250)}
        tmp = tempfile.mkdtemp()
        try:
            exp = {}
            for t in bt.backtest(data, "orb15", 5, cfg):
                exp[(t["date"], t["symbol"])] = (t["side"], t["qty"], t["entry"], t["exit"], t["reason"])
            got = {}
            days = sorted({b.t.date() for b in data["AAA"]})[1:]
            for d in days:
                eng = paper.Engine(cfg, list(data), tmp, mode="replay", log=lambda *_: None)
                paper.replay(eng, data, d)
                for t in eng.state["closed"]:
                    got[(t["date"], t["symbol"])] = (t["side"], t["qty"], t["entry"], t["exit"], t["reason"])
            self.assertTrue(exp, "the synthetic data should produce some trades")
            self.assertEqual(got, exp)
        finally:
            shutil.rmtree(tmp)


class SeparateAccounts(unittest.TestCase):
    def test_each_strategy_has_its_own_trade_cap(self):
        cfg = dict(CFG, strategies=["orb15", "orb30_2r", "orb60"], max_trades_per_day=1)
        data = {"S%d" % i: random_days(4, seed=10 + i) for i in range(12)}
        tmp = tempfile.mkdtemp()
        try:
            days = sorted({b.t.date() for b in data["S0"]})[1:]
            seen = set()
            for d in days:
                eng = paper.Engine(cfg, list(data), tmp, mode="replay", log=lambda *_: None)
                paper.replay(eng, data, d)
                per = {}
                for t in eng.state["closed"]:
                    per[t["strategy"]] = per.get(t["strategy"], 0) + 1
                self.assertTrue(all(n <= 1 for n in per.values()), per)
                self.assertFalse(eng.state["open"], "everything squared off")
                seen |= set(per)
            self.assertGreaterEqual(len(seen), 2, "more than one strategy should trade on the same days")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
