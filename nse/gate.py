"""Real-money readiness gate: has any paper account earned the right to be tried with real money?

The rules below were fixed on 10 Nov 2026, when the accounts had 3 days of results, and must not be
loosened after looking at results: moving the bar to where the numbers already are is overfitting.

An account passes only if ALL of these hold for its LIVE paper trades (never backtests or replays):
  days      at least 60 trading days of paper trading (about 3 months: more than one kind of market)
  trades    at least 150 trades
  profit    average result per trade is above zero after all charges
  luck      t-statistic of R per trade is 3 or more. The usual bar is 2, but 8 accounts run side by
            side and the best of 8 clears 2 by luck alone about one time in six.
  steady    the first half and the second half of its trades are each profitable
  drawdown  its worst losing run never exceeded Rs 10,000 (10 % of the Rs 1 lakh account)
  history   the same account is also profitable on the saved history: the backtest for a strategy,
            the walk-forward test (days the model had not seen) for the learner

Passing means "the evidence supports a small trial", never "safe". Paper fills are kinder than
real ones (no queue, no partial fills, free delayed data), so real results will be worse.
"""
import math

RULES = {"min_days": 60, "min_trades": 150, "min_t": 3.0, "max_drawdown": 10000, "fixed_on": "2026-11-10"}


def t_stat(rs):
    n = len(rs)
    if n < 2:
        return None
    m = sum(rs) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1))
    return round(m / (sd / math.sqrt(n)), 2) if sd else None


def history_of(name, learned):
    """(R per trade, trades, label) for this account on saved history, or None if there is no test yet."""
    if not learned:
        return None
    if name == "learner":
        w = (learned.get("walk_forward") or {}).get("learner") or {}
        if not w.get("trades"):
            return None
        return w["expectancy_r"], w["trades"], "fair test on days it had not seen"
    s = (learned.get("strategies") or {}).get(name)
    if not s or not s.get("trades"):
        return None
    return s["avg_r"], s["trades"], "backtest"


def check_account(name, rows, learned=None, rules=RULES):
    """rows: this account's live journal rows (dicts with date, exit_time, net, r)."""
    rows = sorted(rows, key=lambda r: (r["date"], r.get("exit_time", "")))
    nets = [float(r["net"]) for r in rows]
    rs = [float(r["r"]) for r in rows]
    n, days = len(rows), len({r["date"] for r in rows})
    per = sum(nets) / n if n else 0.0
    t = t_stat(rs)
    half = n // 2
    h1, h2 = sum(nets[:half]), sum(nets[half:])
    eq = peak = dd = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    hist = history_of(name, learned)
    checks = [
        {"id": "days", "need": "%d+ days of paper trading" % rules["min_days"],
         "ok": days >= rules["min_days"], "now": "%d of %d days" % (days, rules["min_days"])},
        {"id": "trades", "need": "%d+ paper trades" % rules["min_trades"],
         "ok": n >= rules["min_trades"], "now": "%d of %d trades" % (n, rules["min_trades"])},
        {"id": "profit", "need": "Profit per trade after charges",
         "ok": n > 0 and per > 0, "now": "Rs %+.0f a trade" % per if n else "no trades yet"},
        {"id": "luck", "need": "Too strong to be luck (t of %g or more)" % rules["min_t"],
         "ok": t is not None and t >= rules["min_t"], "now": "t = %s" % ("%.2f" % t if t is not None else "n/a")},
        {"id": "steady", "need": "Profitable in both halves",
         "ok": n >= 2 and h1 > 0 and h2 > 0, "now": "Rs %+.0f then Rs %+.0f" % (h1, h2) if n >= 2 else "too few trades"},
        {"id": "drawdown", "need": "Worst losing run under Rs %s" % format(rules["max_drawdown"], ","),
         "ok": n > 0 and -dd < rules["max_drawdown"], "now": "Rs %.0f" % dd if n else "no trades yet"},
        {"id": "history", "need": "Also profitable on saved history",
         "ok": bool(hist) and hist[0] > 0,
         "now": "%+.2f R a trade over %d trades (%s)" % hist if hist else "no history test yet"},
    ]
    return {"days": days, "trades": n, "net": round(sum(nets)), "per_trade": round(per), "t": t,
            "drawdown": round(dd), "passed": sum(c["ok"] for c in checks), "of": len(checks),
            "ready": all(c["ok"] for c in checks), "checks": checks}


def readiness(journal, accounts, learned=None, rules=RULES):
    """journal: live rows for every account. Returns the verdict for the phone page and the CLI."""
    acc = {a: check_account(a, [r for r in journal if r.get("strategy") == a], learned, rules) for a in accounts}
    passed = [a for a, v in acc.items() if v["ready"]]
    closest = max(acc, key=lambda a: (acc[a]["passed"], acc[a]["t"] if acc[a]["t"] is not None else -99)) if acc else None
    days = len({r["date"] for r in journal})
    if passed:
        verdict = "Passed: %s. The evidence supports a small real-money trial, not more." % ", ".join(passed)
    else:
        verdict = "No account has passed. %d of the %d days needed so far." % (days, rules["min_days"])
    return {"ready": bool(passed), "passed": passed, "closest": closest, "verdict": verdict, "days": days,
            "rules": rules, "accounts": acc}
