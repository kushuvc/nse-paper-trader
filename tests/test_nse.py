"""Offline tests: python3 -m unittest discover -s tests -v"""
import datetime
import math
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nse import backtest as bt, costs, gate, learn, paper, research, risk, strategies, swing  # noqa: E402
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


def ramp(pct, upto, start=1000.0):
    """Closes that climb steadily to `pct` % by bar index `upto` (0-based) and then stay there."""
    return [round(start * (1 + pct / 100 * min(i + 1, upto + 1) / (upto + 1)), 2) for i in range(75)]


def fired(name, bars, ctx):
    scan = strategies.get(name)
    return [(bars[i].t.strftime("%H:%M"), s) for i in range(len(bars)) for s in [scan(bars[:i + 1], ctx)] if s]


class Round2Ideas(unittest.TestCase):
    D = datetime.date(2026, 3, 2)

    def ctx(self, mkt=None, days=None):
        return {"interval": 5, "prev_close": 1000.0, "prev_high": 1010.0, "prev_low": 990.0,
                "mkt": mkt or {}, "days": days or []}

    def test_leaders_and_its_mirror(self):
        bars = day_bars(self.D, ramp(1.5, 23))            # +1.5 % by 11:15
        got = fired("leaders", bars, self.ctx({"11:10": 0.2}))
        self.assertEqual([t for t, _ in got], ["11:10"])  # only at the bar that ends 11:15
        s = got[0][1]
        self.assertEqual((s.side, s.entry), (1, 1015.0))
        self.assertAlmostEqual(s.stop, 1015 * 0.99, delta=0.05)
        self.assertEqual(fired("laggards", bars, self.ctx({"11:10": 0.2}))[0][1].side, -1)
        self.assertEqual(fired("leaders", bars, self.ctx({"11:10": 0.8})), [])   # only 0.7 % ahead
        self.assertEqual(fired("leaders", bars, self.ctx()), [])                  # market unknown: no trade
        down = day_bars(self.D, ramp(-1.5, 23))
        self.assertEqual(fired("leaders", down, self.ctx({"11:10": 0.0}))[0][1].side, -1)

    def test_market_move_is_read_at_the_entry_bar_only(self):
        bars = day_bars(self.D, ramp(1.5, 23))
        a = fired("leaders", bars, self.ctx({"11:10": 0.2, "11:15": 9.0, "15:25": -9.0}))
        b = fired("leaders", bars, self.ctx({"11:10": 0.2}))
        self.assertEqual(a, b)

    def test_trend_day_needs_the_market_and_a_strong_stock(self):
        bars = day_bars(self.D, ramp(1.5, 23))
        self.assertEqual(fired("trend_day", bars, self.ctx({"11:10": 0.5}))[0][1].side, 1)
        self.assertEqual(fired("trend_day", bars, self.ctx({"11:10": 0.3})), [])    # market too quiet
        self.assertEqual(fired("trend_day", bars, self.ctx({"11:10": -0.5})), [])   # stock is not weak
        self.assertEqual(fired("trend_day", bars, self.ctx({"11:10": 1.8})), [])    # stock behind the market

    def test_late_push(self):
        bars = day_bars(self.D, ramp(1.2, 59))            # +1.2 % by 14:15
        got = fired("late_push", bars, self.ctx({"14:10": 0.6}))
        self.assertEqual([t for t, _ in got], ["14:10"])
        self.assertAlmostEqual(got[0][1].stop, 1012 * 0.9925, delta=0.05)
        self.assertEqual(fired("late_push", bars, self.ctx({"14:10": -0.6})), [])

    def test_daily_trend_only_trades_with_the_trend(self):
        up_days = [(0, c + 5, c - 5, c) for c in range(900, 1000, 5)]          # 20 rising closes
        dn_days = [(0, c + 5, c - 5, c) for c in range(1100, 1000, -5)]
        calm = [1000.0] * 12                                                     # first hour: flat
        brk_up = day_bars(self.D, calm + [1004.0])
        brk_dn = day_bars(self.D, calm + [996.0])
        s = fired("daily_trend", brk_up, self.ctx(days=up_days))[0][1]
        self.assertEqual((s.side, s.stop), (1, 999.5))                           # stop at the first hour's low
        self.assertEqual(fired("daily_trend", brk_dn, self.ctx(days=up_days)), [])
        self.assertEqual(fired("daily_trend", brk_dn, self.ctx(days=dn_days))[0][1].side, -1)
        self.assertEqual(fired("daily_trend", brk_up, self.ctx(days=up_days[:10])), [])  # too little history

    def test_squeeze_needs_the_quietest_day(self):
        wide = [(0, 1010, 990, 1000)] * 6
        brk = day_bars(self.D, [1000.0] * 12 + [1004.0])
        self.assertEqual(len(fired("squeeze", brk, self.ctx(days=wide + [(0, 1002, 998, 1000)]))), 1)
        self.assertEqual(fired("squeeze", brk, self.ctx(days=wide + [(0, 1015, 985, 1000)])), [])

    def test_bounce(self):
        fell = [(0, 0, 0, c) for c in (1050, 1030, 1015, 1000)]                # 3 lower closes, -4.8 %
        green = day_bars(self.D, ramp(0.4, 11))
        got = fired("bounce", green, self.ctx(days=fell))
        self.assertEqual([t for t, _ in got], ["10:10"])                        # the bar that ends 10:15
        self.assertEqual(got[0][1].side, 1)
        self.assertLess(got[0][1].stop, 1000.0)                                 # under today's low
        self.assertEqual(fired("bounce", day_bars(self.D, ramp(-0.4, 11)), self.ctx(days=fell)), [])
        self.assertEqual(fired("bounce", green, self.ctx(days=[(0, 0, 0, c) for c in (1010, 1005, 1002, 1000)])), [])

    def test_study_decisions_do_not_depend_on_later_bars(self):
        data = {"S%d" % k: random_days(30, seed=k, sym_price=500.0 * (k + 1)) for k in range(6)}
        last = max(b.t.date() for b in data["S0"])
        rnd = random.Random(1)
        changed = {}
        for s, bars in data.items():
            f = 1 + rnd.uniform(-0.05, 0.05)
            changed[s] = [b if (b.t.date() != last or b.t.strftime("%H:%M") < "12:00")
                          else Bar(b.t, b.o * f, b.h * f, b.l * f, b.c * f, b.v) for b in bars]
        names = list(strategies.RESEARCH)

        def early(d):
            return {(n, t["symbol"], t["date"], t["time"], t["side"], t["entry"], t["stop"])
                    for n, ts in research.run(d, names, CFG, 5).items() for t in ts
                    if t["date"] != str(last) or t["time"] < "11:55"}
        a = early(data)
        self.assertTrue(a)
        self.assertEqual(a, early(changed))

    def test_exam_is_only_for_ideas_that_made_money_in_the_design_years(self):
        data = {"S%d" % k: random_days(40, seed=20 + k, sym_price=800.0) for k in range(4)}
        res = research.study(data, CFG, 5, exam=True, log=lambda *_: None)
        self.assertEqual(set(res["design"]), set(strategies.RESEARCH))
        self.assertTrue(set(res["exam"]) <= set(res["promoted"]))
        self.assertTrue(set(res["passed"]) <= set(res["exam"]))

    def test_overnight_split(self):
        # every day: opens 1 % above the last close, then gives 0.5 % back by the close
        bars, close = [], 1000.0
        for k in range(4):
            o = close * 1.01
            bars += day_bars(self.D + datetime.timedelta(days=k), [o * 0.995], start_price=o)
            close = o * 0.995
        got = research.overnight_split({"A": bars, "B": bars}, 5)
        self.assertEqual(got["days"], 3)                                         # the first day has no "night before"
        self.assertAlmostEqual(got["overnight_pct_per_day"], 1.0, places=3)
        self.assertAlmostEqual(got["market_hours_pct_per_day"], -0.5, places=3)
        self.assertEqual(got["by_year"], {"2026": {"days": 3, "overnight_total_pct": 3.0, "market_hours_total_pct": -1.5}})
        self.assertEqual((got["stocks"], got["stocks_up_overnight"], got["stocks_up_market_hours"],
                          got["stocks_better_overnight"]), (2, 2, 0, 2))


def dbars(closes, vols=None, start=datetime.date(2015, 1, 1)):
    """Daily bars on weekdays from made-up closes (and volumes)."""
    out, d, i = [], start, 0
    while i < len(closes):
        if d.weekday() < 5:
            c = closes[i]
            out.append(Bar(datetime.datetime.combine(d, datetime.time(9, 15), IST), c, c, c, c, vols[i] if vols else 100000))
            i += 1
        d += datetime.timedelta(days=1)
    return out


def mk(stocks, universe=50, upto=None):
    return swing.build(list(stocks), lambda s: stocks[s], upto=upto, universe=universe)


def span(mkt):
    return mkt.cal[0], mkt.cal[-1]


def walks(n_stocks=10, n_days=700, seed=3, same=False):
    rnd = random.Random(seed)
    out = {}
    path = None
    for n in range(n_stocks):
        if path is None or not same:
            p, path = 100.0, []
            for _ in range(n_days):
                p *= 1 + rnd.gauss(0.0004, 0.02)
                path.append(round(p, 2))
        vols = [int(50000 * (n + 1) * math.exp(rnd.gauss(0, 0.9))) + 1 for _ in range(n_days)]
        out["S%d" % n] = dbars(path, vols)
    return out


class Swing(unittest.TestCase):
    FLAT = [100.0] * 300

    def breakout(self, after):
        """One stock: flat for 300 days, a close at 105 (a new high), then `after`."""
        m = mk({"X": dbars(self.FLAT + [105.0] + after)})
        return m, swing.simulate(m, "high52", *span(m))

    def test_delivery_charges_on_20000(self):
        # STT 20+20, exchange 1.188, SEBI 0.04, stamp 3, GST 18% of (exchange + SEBI), depository 15.93
        exp = 40 + 1.188 + 0.04 + 3 + 0.18 * (1.188 + 0.04) + 15.93
        self.assertAlmostEqual(costs.delivery_charges(20000, 20000), exp, places=4)
        net, gross = costs.delivery_pct(100, 100)
        self.assertEqual(gross, 0)
        self.assertAlmostEqual(net, -0.402, places=2)  # a flat round trip costs about 0.40%

    def test_universe_uses_only_what_was_known(self):
        n = 700
        stocks = {s: dbars([100.0] * n, [v] * n) for s, v in (("A", 1000), ("B", 2000), ("C", 3000), ("D", 4000))}
        stocks["LATE"] = dbars([100.0] * 400, [9600] * 400, start=stocks["A"][300].t.date())  # listed on day 300
        stocks["GROW"] = dbars([100.0] * n, [500] * 450 + [9500] * (n - 450))              # heavily traded from day 450
        stocks["SPLIT"] = dbars([100.0] * 320 + [150.0] * (n - 320), [9900] * n)             # +50% in a day on day 320
        m = mk(stocks, universe=2)
        day = lambda k: m.universe(m.cix[stocks["A"][k].t.date()])
        self.assertEqual(day(100), {})                                    # nobody has 260 days of history yet
        self.assertEqual(sorted(day(300)), ["D", "SPLIT"])
        self.assertEqual(sorted(day(360)), ["C", "D"])                    # SPLIT is out after its 50% day
        self.assertEqual(sorted(day(440)), ["C", "D"])                    # GROW not yet, LATE has under 260 days
        self.assertEqual(sorted(day(540)), ["D", "GROW"])                 # SPLIT still out (252 days), LATE still short
        self.assertEqual(sorted(day(690)), ["LATE", "SPLIT"])             # both back once the rules allow
        self.assertTrue(any(sym == "SPLIT" and pct == 50.0 for _, sym, pct in m.untrusted))

    def test_benchmark_counts_an_untrusted_rise_and_not_an_untrusted_fall(self):
        n = 400
        up = mk({"A": dbars([100.0] * n), "B": dbars([100.0] * 300 + [150.0] * 100)}, universe=2)
        self.assertAlmostEqual(up.held(*span(up)), 0.25, places=6)        # half the money rose 50%
        down = mk({"A": dbars([100.0] * n), "B": dbars([100.0] * 300 + [50.0] * 100)}, universe=2)
        self.assertAlmostEqual(down.held(*span(down)), 0.0, places=6)     # the benchmark is spared the 50% fall

    def test_index_is_a_second_yardstick(self):
        ramp_up = [105.0 + 10.5 * (k + 1) / 20 for k in range(20)]
        closes = self.FLAT + [105.0] + ramp_up + [115.5] * 40
        index = dbars([1000.0] * 300 + [1000.0 * 1.002 ** (k + 1) for k in range(61)])   # index rises 0.2% a day
        m = swing.build(["X"], lambda s: dbars(closes), index=index)
        row = swing.price(m, swing.simulate(m, "high52", *span(m)))[0]
        self.assertAlmostEqual(row["net_pct"] - row["vs_index_pct"], (1.002 ** 20 - 1) * 100, places=6)
        s = swing.summary([row])
        self.assertLess(s["excess_pct"], 0)                                # the only stock IS the benchmark: it loses the charges
        self.assertFalse(swing.qualifies(s))
        s = dict(s, excess_pct=0.5)
        self.assertTrue(swing.qualifies(s))                                # +9.6% after charges, index +4.1%
        self.assertFalse(swing.qualifies(dict(s, vs_index_pct=-0.1)))
        self.assertFalse(swing.qualifies(dict(s, excess_pct=-0.1)))
        self.assertFalse(swing.qualifies(dict(s, net_pct=-0.1)))
        cut = swing.build(["X"], lambda s: dbars(closes), index=index, upto=m.cal[310])
        self.assertLess(cut.index[-1][0], m.cal[310])                      # no index price from the cut onwards either

    def test_momentum_holds_the_past_winners(self):
        stocks = {"S%d" % i: dbars([100 * (1 + 0.0002 * i) ** k for k in range(600)]) for i in range(6)}
        m = mk(stocks, universe=6)
        tr = swing.simulate(m, "momentum", *span(m), slots=2)
        self.assertEqual(sorted(t.sym for t in tr), ["S4", "S5"])
        self.assertTrue(all(t.why == "end" for t in tr))
        rows = swing.price(m, tr)
        self.assertTrue(all(r["excess_pct"] > 0 for r in rows))           # they beat the six held equally

    def test_low_vol_holds_the_steadiest(self):
        stocks = {}
        for i in range(5):
            p, path = 100.0, []
            for k in range(600):
                p *= 1 + (0.002 * (i + 1) if k % 2 else -0.002 * (i + 1))
                path.append(p)
            stocks["S%d" % i] = dbars(path)
        m = mk(stocks, universe=5)
        self.assertEqual({t.sym for t in swing.simulate(m, "low_vol", *span(m), slots=2)}, {"S0", "S1"})

    def test_losers_buys_the_weeks_biggest_faller_for_a_week(self):
        n = 400
        flat = [100.0] * n
        dip = list(flat)
        dates = [b.t.date() for b in dbars(flat)]
        wed = next(k for k in range(300, n) if dates[k].weekday() == 2)
        for k in range(wed, n):
            dip[k] = 95.0
        stocks = {"A": dbars(flat, [3000] * n), "B": dbars(flat, [2000] * n), "DIP": dbars(dip, [1000] * n)}
        m = mk(stocks, universe=3)
        tr = [t for t in swing.simulate(m, "losers", *span(m), slots=1) if t.sym == "DIP"]
        self.assertEqual(len(tr), 1)
        s = m.s["DIP"]
        self.assertEqual((s.d[tr[0].ke].weekday(), tr[0].ke - wed), (4, 2))  # bought at Friday's close, two days after the fall
        self.assertEqual(tr[0].kx - tr[0].ke, 5)                             # sold a week later

    def test_high52_entry_time_exit_and_stop(self):
        m, tr = self.breakout([105.0] * 40)
        self.assertEqual([(t.ke, t.kx, t.why) for t in tr], [(300, 320, "rule")])       # 20 trading days
        m, tr = self.breakout([104.0, 96.0] + [96.0] * 40)                            # 96 <= 0.92 * 105
        self.assertEqual([(t.ke, t.kx) for t in tr], [(300, 302)])
        self.assertAlmostEqual(swing.price(m, tr)[0]["gross_pct"], (96 / 105 - 1) * 100, places=6)
        m = mk({"X": dbars(self.FLAT)})
        self.assertEqual(swing.simulate(m, "high52", *span(m)), [])

    def test_pullback_buys_a_dip_in_an_uptrend_only(self):
        up = [100 * 1.001 ** k for k in range(300)]
        dip = [up[-1] * 0.98, up[-1] * 0.96, up[-1] * 0.94]
        m = mk({"X": dbars(up + dip + [dip[-1] * 1.03] * 20)})
        tr = swing.simulate(m, "pullback", *span(m))
        # day 303 is up 3% but still under its 5-day average (which holds the pre-dip close); day 304 is above it
        self.assertEqual([(t.ke, t.kx) for t in tr], [(302, 304)])
        down = [100 * 0.999 ** k for k in range(300)]
        dip = [down[-1] * 0.98, down[-1] * 0.96, down[-1] * 0.94]
        m = mk({"X": dbars(down + dip + [dip[-1] * 1.03] * 20)})
        self.assertEqual(swing.simulate(m, "pullback", *span(m)), [])    # below its 200-day average: no trade

    def test_volume_surge_needs_both_the_rise_and_the_volume(self):
        closes = self.FLAT + [105.0] * 40
        m = mk({"X": dbars(closes, [1000] * 300 + [4000] + [1000] * 39)})
        self.assertEqual([(t.ke, t.kx) for t in swing.simulate(m, "volume_surge", *span(m))], [(300, 310)])
        m = mk({"X": dbars(closes, [1000] * 340)})
        self.assertEqual(swing.simulate(m, "volume_surge", *span(m)), [])
        m = mk({"X": dbars(self.FLAT + [103.0] * 40, [1000] * 300 + [4000] + [1000] * 39)})
        self.assertEqual(swing.simulate(m, "volume_surge", *span(m)), [])

    def test_untrusted_jump_is_not_credited_and_a_crash_counts(self):
        m, tr = self.breakout([105.0, 105.0, 160.0] + [160.0] * 30)       # +52% in a day
        self.assertEqual([(t.ke, t.kx, t.why) for t in tr], [(300, 302, "jump")])
        row = swing.price(m, tr)[0]
        self.assertEqual(row["gross_pct"], 0)
        self.assertLess(row["net_pct"], 0)
        m, tr = self.breakout([105.0, 105.0, 50.0] + [50.0] * 30)         # -52% in a day
        self.assertEqual([(t.ke, t.kx, t.why) for t in tr], [(300, 303, "rule")])
        self.assertAlmostEqual(swing.price(m, tr)[0]["gross_pct"], (50 / 105 - 1) * 100, places=6)

    def test_decisions_do_not_use_later_prices(self):
        stocks = walks()
        full = mk(stocks, universe=6)
        cut = full.cal[560]
        part = mk(stocks, universe=6, upto=cut)
        last = part.cal[-1]
        self.assertLess(last, cut)
        for name in swing.IDEAS:
            a = swing.simulate(full, name, *span(full))
            b = swing.simulate(part, name, *span(part))
            self.assertTrue(a, name)
            key = lambda m, t: (t.sym, m.s[t.sym].d[t.ke])
            ea = {key(full, t): t for t in a if key(full, t)[1] < last}
            eb = {key(part, t): t for t in b if key(part, t)[1] < last}
            self.assertEqual(sorted(ea), sorted(eb), name)                # the same buys ...
            for k, t in ea.items():
                if full.s[t.sym].d[t.kx] < last:
                    self.assertEqual((t.kx, t.why), (eb[k].kx, eb[k].why), name)  # ... and the same sells

    def test_riding_the_market_earns_no_excess(self):
        m = mk(walks(same=True), universe=6)
        for name in ("losers", "high52", "momentum"):
            rows = swing.price(m, swing.simulate(m, name, *span(m)))
            self.assertTrue(rows, name)
            for r in rows:
                self.assertAlmostEqual(r["bench_pct"], r["gross_pct"], places=6)
                self.assertTrue(-0.6 < r["excess_pct"] < -0.3, r)         # exactly the charges, nothing else

    def test_account_and_day_late(self):
        ramp_up = [105.0 + 10.5 * (k + 1) / 20 for k in range(20)]
        m, tr = self.breakout(ramp_up + [115.5] * 40)
        self.assertEqual([(t.ke, t.kx) for t in tr], [(300, 320)])
        row = swing.price(m, tr)[0]
        self.assertAlmostEqual(row["gross_pct"], 10.0, places=6)
        self.assertEqual(row["days"], 20)
        acct = swing.account(m, tr, *span(m))
        self.assertAlmostEqual(acct["final"], 100000 + 20000 * row["net_pct"] / 100, delta=1)
        self.assertEqual(acct["trades_taken"], 1)
        late = swing.price(m, tr, delay=1)[0]
        self.assertAlmostEqual(late["gross_pct"], (115.5 / ramp_up[0] - 1) * 100, places=6)

    def test_summary_judges_the_excess_month_by_month(self):
        rows = [{"entry_date": "2020-%02d-10" % mth, "days": 5, "gross_pct": 1.4, "net_pct": 1.0, "bench_pct": 0.5,
                 "excess_pct": x} for mth, xs in ((1, [1.0] * 30), (2, [-1.0]), (3, [-0.5])) for x in xs]
        s = swing.summary(rows)
        self.assertEqual((s["trades"], s["months"]), (32, 3))
        self.assertGreater(s["excess_pct"], 0.8)                          # 30 good trades in one month ...
        self.assertLess(abs(s["t_excess_by_month"]), 1)                   # ... are one good month, not 30 proofs
        self.assertEqual(s["by_year"]["2020"]["trades"], 32)


if __name__ == "__main__":
    unittest.main()
