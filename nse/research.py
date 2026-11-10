"""Round 2: can a different KIND of strategy do better? Tested so that the answer can be believed.

Protocol, written on 10 Nov 2026 before any of these ideas had been run on any data:

1. Seven ideas (strategies.RESEARCH). Each has a reason to exist, fixed round-number settings and
   a wide stop. There is no search over settings.
2. Three years of hourly bars are cut in two at SPLIT:
     design years   before 1 Nov 2025    (478 days)  every idea is run here first
     exam year      1 Nov 2025 onwards   (246 days)  not looked at while choosing
3. An idea goes to the exam only if it made money after all charges in the design years.
4. It passes the exam only if, in the exam year, it made money after charges AND its t-statistic
   BY DAY is EXAM_MIN_T or more. By day, not by trade: stocks move together, so 30 trades on one
   day are closer to one bet than to 30. 2.5, not the usual 2, because seven ideas are tried.
5. An idea that passes becomes a paper account and must then pass the real-money gate
   (nse/gate.py) like every other account. Passing the exam is permission to paper trade only.
6. Every idea tried is reported, including the ones that fail.
"""
import math

from . import backtest as bt, learn, strategies
from .market import full_sessions

SPLIT = "2025-11-01"
EXAM_MIN_T = 2.5
KEEP_DAYS = 25
REGISTERED = "2026-11-10"


def run(bars_by_sym, names, cfg, interval=60):
    """Every signal of every idea on every stock and day. Returns {name: [trades]}."""
    sess = {s: full_sessions(b, interval) for s, b in bars_by_sym.items()}
    by_day = {}
    for s, ss in sess.items():
        for d, bars in ss:
            by_day.setdefault(d, {})[s] = bars
    mkt = {d: learn.market_moves(v) for d, v in by_day.items()}
    scans = {n: strategies.get(n) for n in names}
    out = {n: [] for n in names}
    for sym, ss in sess.items():
        ohlc = [(s[0].o, max(b.h for b in s), min(b.l for b in s), s[-1].c) for _, s in ss]
        for k in range(1, len(ss)):
            d, bars = ss[k]
            prev = ss[k - 1][1]
            ctx = {"interval": interval, "prev_close": prev[-1].c,
                   "prev_high": max(b.h for b in prev), "prev_low": min(b.l for b in prev),
                   "mkt": mkt[d], "days": ohlc[max(0, k - KEEP_DAYS):k]}
            for n, scan in scans.items():
                t = bt.run_session(scan, bars, ctx, cfg)
                if t:
                    t["symbol"], t["strategy"] = sym, n
                    out[n].append(t)
    for v in out.values():
        v.sort(key=lambda t: (t["date"], t["time"], t["symbol"]))
    return out


def _t(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return round(m / (sd / math.sqrt(n)), 2) if sd else None


def summary(trades):
    n = len(trades)
    if not n:
        return {"trades": 0}
    by_day = {}
    for t in trades:
        by_day.setdefault(t["date"], []).append(t["r"])
    daily = [sum(v) / len(v) for v in by_day.values()]
    return {
        "trades": n, "days": len(daily),
        "win_rate": round(100 * sum(t["net"] > 0 for t in trades) / n, 1),
        "r_before_charges": round(sum(t["gross"] / t["risk_rs"] for t in trades if t["risk_rs"]) / n, 3),
        "r_after_charges": round(sum(t["r"] for t in trades) / n, 3),
        "rs_per_trade": round(sum(t["net"] for t in trades) / n, 1),
        "total_net": round(sum(t["net"] for t in trades)),
        "t_by_day": _t(daily),
    }


def overnight_split(bars_by_sym, interval=60):
    """Where did the stocks' returns come from: while the market was shut (yesterday's close to
    today's open) or while it was open (open to close)? Average of all stocks, per day."""
    night, day = {}, {}
    for s, b in bars_by_sym.items():
        ss = full_sessions(b, interval)
        for (_, p), (d, c) in zip(ss, ss[1:]):
            on = (c[0].o / p[-1].c - 1) * 100
            if abs(on) > 15:  # split or bonus, not a real move
                continue
            night.setdefault(d, []).append(on)
            day.setdefault(d, []).append((c[-1].c / c[0].o - 1) * 100)
    n_ = [sum(v) / len(v) for _, v in sorted(night.items())]
    d_ = [sum(v) / len(v) for _, v in sorted(day.items())]
    if not n_:
        return {}
    return {"days": len(n_),
            "overnight_pct_per_day": round(sum(n_) / len(n_), 4), "overnight_t": _t(n_),
            "overnight_total_pct": round(sum(n_), 1),
            "market_hours_pct_per_day": round(sum(d_) / len(d_), 4), "market_hours_t": _t(d_),
            "market_hours_total_pct": round(sum(d_), 1)}


def study(bars_by_sym, cfg, interval=60, exam=False, log=print):
    names = list(strategies.RESEARCH)
    trades = run(bars_by_sym, names, cfg, interval)
    res = {"registered": REGISTERED, "split": SPLIT, "exam_min_t": EXAM_MIN_T, "exam_opened": bool(exam),
           "ideas": {n: strategies.RESEARCH[n][2] for n in names}, "design": {}, "exam": {}, "promoted": [],
           "passed": [], "overnight": overnight_split(bars_by_sym, interval)}
    head = "%-12s %7s %6s %7s %9s %9s %9s %7s" % ("idea", "trades", "days", "win %", "R before", "R after", "Rs/trade", "t(day)")

    def line(n, s):
        return "%-12s %7d %6d %7.1f %9.3f %9.3f %9.1f %7s" % (
            n, s["trades"], s["days"], s["win_rate"], s["r_before_charges"], s["r_after_charges"],
            s["rs_per_trade"], s["t_by_day"]) if s["trades"] else "%-12s %7d" % (n, 0)

    log("DESIGN YEARS (before %s). 'R before/after' = result per trade before/after charges, in units of risk." % SPLIT)
    log(head)
    for n in names:
        s = summary([t for t in trades[n] if t["date"] < SPLIT])
        res["design"][n] = s
        if s["trades"] and s["rs_per_trade"] > 0:
            res["promoted"].append(n)
        log(line(n, s))
    log("\nMade money after charges in the design years, so allowed into the exam: %s" % (", ".join(res["promoted"]) or "none"))
    if exam:
        log("\nEXAM YEAR (%s onwards), only for the ideas above. Pass = profit after charges and t(day) >= %g." % (SPLIT, EXAM_MIN_T))
        log(head)
        for n in res["promoted"]:
            ex = [t for t in trades[n] if t["date"] >= SPLIT]
            s = summary(ex)
            s["three_a_day"] = summary(bt.portfolio(ex, cfg))
            res["exam"][n] = s
            ok = bool(s["trades"]) and s["rs_per_trade"] > 0 and (s["t_by_day"] or 0) >= EXAM_MIN_T
            if ok:
                res["passed"].append(n)
            log(line(n, s) + ("   PASS" if ok else "   fail"))
        log("\nPassed the exam: %s" % (", ".join(res["passed"]) or "none"))
    o = res["overnight"]
    if o:
        log("\nWhere the %d days' returns came from (average stock, before any charges):" % o["days"])
        log("  while the market was shut (close to next open): %+.3f%% a day, %+.0f%% in total (t %s)" % (
            o["overnight_pct_per_day"], o["overnight_total_pct"], o["overnight_t"]))
        log("  while the market was open (open to close):      %+.3f%% a day, %+.0f%% in total (t %s)" % (
            o["market_hours_pct_per_day"], o["market_hours_total_pct"], o["market_hours_t"]))
    return res
