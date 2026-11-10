"""Offline tests: python3 -m unittest discover -s tests -v"""
import datetime
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nse import backtest as bt, costs, gate, learn, paper, risk, strategies  # noqa: E402
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


class Learner(unittest.TestCase):
    def fake(self, day, r, n, **f):
        base = {"side": "long", "time": "before 10:30", "market": "flat market", "gap": "no gap",
                "stop": "normal stop"}
        return [{"strategy": "s", "day": day, "r": r, "f": dict(base, **f)} for _ in range(n)]

    def test_small_samples_do_not_move_the_model(self):
        tr = []
        for d in range(25):
            tr += self.fake(d, -0.2, 20)
        tr += self.fake(24, 3.0, 5, market="with market")  # 5 lucky trades: not enough to act on
        m = learn.Model.fit(tr, 25)
        self.assertEqual(m.d["strategies"]["s"]["cells"]["market"]["with market"]["delta"], 0.0)
        self.assertFalse(m.approve("s", dict(tr[-1]["f"]))[0])

    def test_strong_evidence_does(self):
        tr = []
        for d in range(25):
            tr += self.fake(d, -0.3, 20) + self.fake(d, 0.6, 20, market="with market")
        m = learn.Model.fit(tr, 25)
        self.assertTrue(m.approve("s", dict(tr[-1]["f"]))[0])
        self.assertFalse(m.approve("s", dict(tr[0]["f"]))[0])

    def test_estimates_are_shrunk_toward_zero(self):
        tr = []
        for d in range(25):
            tr += self.fake(d, 1.0, 2)
        m = learn.Model.fit(tr, 25)
        self.assertLess(m.d["strategies"]["s"]["r"], 1.0)
        self.assertGreater(m.d["strategies"]["s"]["r"], 0.0)

    def test_fit_never_sees_the_day_it_decides(self):
        tr = []
        for d in range(25):
            tr += self.fake(d, -0.2, 20)
        tr += self.fake(25, 5.0, 500)  # a huge win on the day being decided must not leak in
        a, b = learn.Model.fit(tr, 25), learn.Model.fit(tr[:-500], 25)
        self.assertEqual(a.d, b.d)

    def test_no_trades_during_warmup(self):
        tr = []
        for d in range(5):
            tr += self.fake(d, 2.0, 50)
        self.assertFalse(learn.Model.fit(tr, 5).approve("s", tr[0]["f"])[0])

    def test_live_learner_matches_walk_forward(self):
        """The paper engine's learner account takes exactly the trades the walk-forward test credits it with."""
        cfg = dict(CFG, strategies=["orb15", "orb60"], max_trades_per_day=50, max_daily_loss=10 ** 9)
        data = {"S%d" % i: random_days(26, seed=40 + i, sym_price=200 + 50 * i) for i in range(6)}
        p = {"min_edge_r": -9.0, "warmup_days": 3}  # approve everything after warm-up: tests the plumbing
        trades, days = learn.labelled_trades(data, cfg["strategies"], cfg)
        taken, _, _ = learn.walk_forward(trades, len(days), cfg, p)
        exp = {(t["date"], t["symbol"], t["strategy"], t["entry"]) for t in taken
               if t["date"] in (str(days[-1]), str(days[-2]))}
        tmp = tempfile.mkdtemp()
        try:
            got = set()
            for D in (len(days) - 2, len(days) - 1):
                eng = paper.Engine(cfg, list(data), tmp, mode="replay", log=lambda *_: None,
                                   model=learn.Model.fit(trades, D, p))
                paper.replay(eng, data, days[D])
                got |= {(t["date"], t["symbol"], t["via"], t["entry"]) for t in eng.state["closed"]
                        if t["strategy"] == "learner"}
            self.assertTrue(exp)
            # walk-forward's portfolio() lets a stock re-enter once flat; the live account does too
            self.assertEqual(got, exp)
        finally:
            shutil.rmtree(tmp)


def journal_rows(name, ndays, pattern, per_day=3):
    """Live journal rows: `per_day` trades a day, R values cycling through `pattern`, Rs 500 a R."""
    rows, d0, i = [], datetime.date(2026, 1, 1), 0
    for d in range(ndays):
        for k in range(per_day):
            r = pattern[i % len(pattern)]
            i += 1
            rows.append({"strategy": name, "date": str(d0 + datetime.timedelta(days=d)),
                         "exit_time": "1%d:00" % k, "net": "%.2f" % (r * 500), "r": "%.3f" % r})
    return rows


GOOD = [1.0, 1.0, -0.9]                       # +0.37 R a trade
HIST_GOOD = {"strategies": {"orb15": {"trades": 900, "avg_r": 0.08}}}
HIST_BAD = {"strategies": {"orb15": {"trades": 900, "avg_r": -0.10}}}


class Gate(unittest.TestCase):
    def failed(self, res):
        return {c["id"] for c in res["checks"] if not c["ok"]}

    def test_three_lucky_days_are_not_ready(self):
        res = gate.check_account("orb15", journal_rows("orb15", 3, [1.0, 0.8, 1.2]), HIST_GOOD)
        self.assertFalse(res["ready"])
        self.assertEqual(self.failed(res), {"days", "trades"})

    def test_long_strong_record_with_good_history_passes(self):
        res = gate.check_account("orb15", journal_rows("orb15", 70, GOOD), HIST_GOOD)
        self.assertEqual(self.failed(res), set())
        self.assertTrue(res["ready"])
        self.assertGreaterEqual(res["t"], 3)

    def test_same_record_fails_when_history_loses_or_is_missing(self):
        rows = journal_rows("orb15", 70, GOOD)
        self.assertEqual(self.failed(gate.check_account("orb15", rows, HIST_BAD)), {"history"})
        self.assertEqual(self.failed(gate.check_account("orb15", rows, None)), {"history"})

    def test_learner_is_judged_on_its_walk_forward_test(self):
        rows = journal_rows("learner", 70, GOOD)
        bad = {"walk_forward": {"learner": {"trades": 80, "expectancy_r": -0.07}}}
        good = {"walk_forward": {"learner": {"trades": 80, "expectancy_r": 0.07}}}
        self.assertEqual(self.failed(gate.check_account("learner", rows, bad)), {"history"})
        self.assertTrue(gate.check_account("learner", rows, good)["ready"])

    def test_good_start_then_fading_is_not_ready(self):
        rows = journal_rows("orb15", 35, GOOD) + [
            dict(r, date=str(datetime.date(2026, 3, 1) + datetime.timedelta(days=i // 3)))
            for i, r in enumerate(journal_rows("orb15", 35, [1.0, -1.0, -0.3]))]
        res = gate.check_account("orb15", rows, HIST_GOOD)
        self.assertGreater(res["net"], 0)                  # still up overall ...
        self.assertIn("steady", self.failed(res))          # ... but the second half lost
        self.assertFalse(res["ready"])

    def test_deep_losing_run_is_not_ready(self):
        rows = journal_rows("orb15", 70, [2.0] * 100 + [-1.0] * 25 + [2.0] * 85)
        res = gate.check_account("orb15", rows, HIST_GOOD)
        self.assertEqual(res["drawdown"], -12500)
        self.assertEqual(self.failed(res), {"drawdown"})

    def test_weak_profit_is_not_enough(self):
        # +0.03 R a trade over 210 trades: positive, but well inside luck
        res = gate.check_account("orb15", journal_rows("orb15", 70, [1.0, -0.94]), HIST_GOOD)
        self.assertGreater(res["per_trade"], 0)
        self.assertIn("luck", self.failed(res))

    def test_readiness_verdict(self):
        names = ["orb15", "vwap", "learner"]
        g = gate.readiness(journal_rows("orb15", 3, GOOD) + journal_rows("vwap", 3, [-1.0]), names, HIST_GOOD)
        self.assertFalse(g["ready"])
        self.assertEqual(g["verdict"], "No account has passed. 3 of the 60 days needed so far.")
        self.assertEqual(g["accounts"]["learner"]["trades"], 0)
        g = gate.readiness(journal_rows("orb15", 70, GOOD), names, HIST_GOOD)
        self.assertEqual(g["passed"], ["orb15"])
        self.assertIn("small real-money trial", g["verdict"])


if __name__ == "__main__":
    unittest.main()
