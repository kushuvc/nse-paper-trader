"""Learning from mistakes, every day, without overfitting.

Every evening, after the day's 5-minute bars are saved, the learner re-tests
every strategy on ALL the 5-minute history (it grows by a day each evening)
and records, for every trade, a few plain facts known at the moment of entry:

  side    long / short
  time    before 10:30 / 10:30-13:00 / after 13:00
  market  with market / against market / flat market
          (Nifty 50 stocks' average move since the open, at that moment)
  gap     with gap / against gap / no gap (the stock's own opening gap)
  stop    tight stop / normal stop / wide stop (stop distance as % of price)

Since 10 Nov 2026 two more facts come from NSE's own end-of-day file of the
session BEFORE (nse/official.py), so both are known before the market opens:

  delivery  after high / low / usual delivery: the share of the stock's volume
            taken for delivery yesterday, against its own average over the 20
            days before (1.2 times or more = high, 0.8 times or less = low)
  breadth   with / against yesterday's market, or after a mixed market: 60% or
            more of all NSE shares rose yesterday = an up day, 40% or fewer = a
            down day, compared with the side of the trade

Their thresholds were fixed before their effect was looked at, and they obey
every rule below. Each night the walk-forward test is also run WITHOUT them, so
whether NSE's data helps is measured, not assumed.

It then learns which strategies, and which of these conditions, have been
losing money (the "mistakes") and which have done better. The next morning a
separate paper account, "learner", takes only the signals the model expects
to make money after costs.

How it avoids overfitting (fitting noise that won't repeat):
  * Few, coarse facts, chosen in advance. No indicator soup, no parameter tuning.
    Each fact is judged on its own, never in combinations (1,458 combos per
    strategy would be pure noise at this data size).
  * Shrinkage: every estimate starts at "no edge" (0 R) and moves away only as
    evidence piles up (PRIOR trades' worth of doubt). A pattern seen in 40
    trades barely counts; one seen in 2,000 counts.
  * Ignore small samples: a condition needs MIN_TRADES before it changes anything.
  * Forgetting: older trades count less (half-life HALF_LIFE trading days), so
    the model adapts to how the market behaves now, but slowly.
  * A margin: a signal is taken only if the expected result after costs is at
    least MIN_EDGE R, not merely above zero.
  * Walk-forward test: for every past day, the model is fitted ONLY on the
    days before it and judged on that day. That out-of-sample record, not the
    fit, is what tells you whether the learning helps.

Same code at night (fit), in the walk-forward test and in the live engine
(features at the entry bar), so what is tested is what trades.
"""
import datetime
import itertools
import json
import math
import os

from . import backtest as bt
from .market import full_sessions, minutes

PARAMS = {
    "half_life_days": 60,  # a trade 60 trading days old counts half as much as yesterday's
    "prior_trades": 50,    # shrinkage strength: doubt worth 50 trades of "no edge"
    "min_trades": 30,      # a condition needs this many trades to change anything
    "min_edge_r": 0.05,    # take a signal only if it is expected to make >= 0.05 R after costs
    "warmup_days": 20,     # no learner trades until it has this many days of history
}
VALUES = {
    "side": ("long", "short"),
    "time": ("before 10:30", "10:30-13:00", "after 13:00"),
    "market": ("with market", "against market", "flat market"),
    "gap": ("with gap", "against gap", "no gap"),
    "stop": ("tight stop", "normal stop", "wide stop"),
}


# Facts from NSE's own file of the session before. Missing numbers give no fact, and no fact changes nothing.
NSE_VALUES = {
    "delivery": ("after high delivery", "after low delivery", "after usual delivery"),
    "breadth": ("with yesterday's market", "against yesterday's market", "after a mixed market"),
}
ALL_VALUES = dict(VALUES, **NSE_VALUES)


# ------------------------------------------------------------------ facts at entry
def nse_facts(side, known):
    """`known` is official.Context.before(): yesterday's delivery against the share's own 20-day
    average, and the share of all NSE shares that rose yesterday."""
    out = {}
    r = (known or {}).get("deliv_ratio")
    if r is not None:
        out["delivery"] = "after high delivery" if r >= 1.2 else ("after low delivery" if r <= 0.8 else "after usual delivery")
    a = (known or {}).get("adv_pct")
    if a is not None:
        day = 1 if a >= 60 else (-1 if a <= 40 else 0)
        out["breadth"] = ("after a mixed market" if not day else
                          "with yesterday's market" if day * side > 0 else "against yesterday's market")
    return out


def market_moves(today_by_sym):
    """{symbol: today's bars} -> {'HH:MM' bar start: average % move from the day's open across stocks}."""
    acc = {}
    for bars in today_by_sym.values():
        if not bars or bars[0].o <= 0:
            continue
        o = bars[0].o
        for b in bars:
            k = b.t.strftime("%H:%M")
            s, n = acc.get(k, (0.0, 0))
            acc[k] = (s + (b.c / o - 1) * 100, n + 1)
    return {k: s / n for k, (s, n) in acc.items()}


def features(side, entry, stop, today, prev_close, mkt_move, interval=5, nse=None):
    """Facts known when the entry bar (today[-1]) closes. Thresholds fixed in advance.
    `nse`: official.Context.before() for this stock, or None."""
    end = minutes(today[-1].t) + interval
    gap = (today[0].o - prev_close) / prev_close * 100 if prev_close else 0.0
    stop_pct = abs(entry - stop) / entry * 100 if entry else 0.0
    mv = mkt_move or 0.0
    return dict({
        "side": "long" if side > 0 else "short",
        "time": "before 10:30" if end <= 10 * 60 + 30 else ("10:30-13:00" if end <= 13 * 60 else "after 13:00"),
        "market": "flat market" if abs(mv) < 0.25 else ("with market" if mv * side > 0 else "against market"),
        "gap": "no gap" if abs(gap) < 0.5 else ("with gap" if gap * side > 0 else "against gap"),
        "stop": "tight stop" if stop_pct < 0.4 else ("wide stop" if stop_pct > 1.0 else "normal stop"),
    }, **nse_facts(side, nse))


def describe(f):
    return ", ".join(f[k] for k in ALL_VALUES if k in f)


# ------------------------------------------------------------------ training data
def labelled_trades(bars_by_sym, names, cfg, interval=5, nse=None):
    """Every strategy's backtest trades on all saved history, each tagged with its entry facts.
    `nse`: official.Context, for the two facts from NSE's file of the session before.
    Returns (trades, trading days in order)."""
    sess = {s: full_sessions(b, interval) for s, b in bars_by_sym.items()}
    by_day, prev_close, prev_day = {}, {}, {}
    for s, ss in sess.items():
        for k, (d, bars) in enumerate(ss):
            by_day.setdefault(d, {})[s] = bars
            if k:
                prev_close[(s, d)] = ss[k - 1][1][-1].c
                prev_day[(s, d)] = ss[k - 1][0]
    mkt = {d: market_moves(v) for d, v in by_day.items()}
    days = sorted(by_day)
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for name in names:
        for t in bt.backtest(bars_by_sym, name, interval, cfg):
            d = datetime.date.fromisoformat(t["date"])
            today = by_day[d][t["symbol"]]
            k = next(i for i, b in enumerate(today) if b.t.strftime("%H:%M") == t["time"])
            t["strategy"], t["day"] = name, idx[d]
            t["f"] = features(t["side"], t["entry"], t["stop"], today[:k + 1], prev_close[(t["symbol"], d)],
                              mkt[d].get(t["time"]), interval,
                              nse.before(t["symbol"], prev_day[(t["symbol"], d)]) if nse else None)
            out.append(t)
    out.sort(key=lambda t: (t["date"], t["time"], t["symbol"]))
    return out, days


# ------------------------------------------------------------------ the model
class Model:
    def __init__(self, d):
        self.d = d

    @classmethod
    def fit(cls, trades, upto, p=None):
        """Learn from trades on days < upto (day numbers). Decisions apply to day `upto`."""
        p = dict(PARAMS, **(p or {}))
        hl, K, MN = p["half_life_days"], p["prior_trades"], p["min_trades"]
        agg, seen = {}, set()
        for t in trades:
            if t["day"] >= upto:
                continue
            seen.add(t["day"])
            w = 0.5 ** ((upto - t["day"]) / hl)
            a = agg.setdefault(t["strategy"], {"w": 0.0, "s": 0.0, "n": 0, "c": {}})
            a["w"] += w
            a["s"] += w * t["r"]
            a["n"] += 1
            for f, v in t["f"].items():
                c = a["c"].setdefault(f, {}).setdefault(v, [0.0, 0.0, 0])
                c[0] += w
                c[1] += w * t["r"]
                c[2] += 1
        strats = {}
        for name, a in agg.items():
            m = a["s"] / (a["w"] + K)  # shrunk toward 0 R
            cells = {}
            for f, vals in a["c"].items():
                for v, (w, s, n) in vals.items():
                    mc = (s + K * m) / (w + K)  # shrunk toward the strategy's own average
                    cells.setdefault(f, {})[v] = {
                        "n": n, "raw_r": round(s / w, 3) if w else 0.0, "r": round(mc, 3),
                        "delta": round(mc - m, 3) if w >= MN else 0.0}
            strats[name] = {"n": a["n"], "raw_r": round(a["s"] / a["w"], 3) if a["w"] else 0.0,
                            "r": round(m, 3), "cells": cells}
        facts = [k for k in ALL_VALUES if any(k in s["cells"] for s in strats.values())]
        return cls({"params": p, "days": len(seen), "facts": facts, "strategies": strats})

    def facts(self):
        """The facts this model was fitted on (older saved models: the first five)."""
        return self.d.get("facts") or list(VALUES)

    def total_setups(self):
        """How many fact combinations the model can tell apart."""
        return math.prod(len(ALL_VALUES[k]) for k in self.facts())

    def score(self, name, f):
        s = self.d["strategies"].get(name)
        if s is None:
            return None
        return round(s["r"] + sum(s["cells"].get(k, {}).get(v, {}).get("delta", 0.0) for k, v in f.items()), 3)

    def approve(self, name, f):
        """(take?, expected R after costs)"""
        if self.d.get("days", 0) < self.d["params"]["warmup_days"]:
            return False, None
        sc = self.score(name, f)
        return (sc is not None and sc >= self.d["params"]["min_edge_r"]), sc

    def allowed_setups(self, name):
        """How many of the possible fact combinations (total_setups) this strategy would currently take."""
        keys, n = self.facts(), 0
        for combo in itertools.product(*(ALL_VALUES[k] for k in keys)):
            if self.approve(name, dict(zip(keys, combo)))[0]:
                n += 1
        return n

    def lessons(self, top=12):
        p = self.d["params"]
        out = []
        for name, s in self.d["strategies"].items():
            for f, vals in s["cells"].items():
                for v, c in vals.items():
                    if abs(c["delta"]) >= 0.05:
                        out.append((abs(c["delta"]) * math.sqrt(c["n"]), "%s %s: %+.2f R a trade over %d trades "
                                    "(strategy average %+.2f R) -> %s" % (
                                        name, v, c["raw_r"], c["n"], s["raw_r"],
                                        "avoid" if c["delta"] < 0 else "better than usual")))
        out.sort(reverse=True)
        return [x[1] for x in out[:top]] or ["Nothing stands out yet: no condition differs enough, on enough "
                                             "trades (%d+), to act on." % p["min_trades"]]

    @classmethod
    def load(cls, path):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return cls(json.load(f)["model"])


# ------------------------------------------------------------------ honest check
def walk_forward(trades, ndays, cfg, p=None):
    """Each day judged by a model fitted only on the days before it. Returns
    (learner trades after the account's caps, every-signal trades after the same caps, per-day rows)."""
    p = dict(PARAMS, **(p or {}))
    taken, base, rows = [], [], []
    by_day = {}
    for t in trades:
        by_day.setdefault(t["day"], []).append(t)
    for D in range(p["warmup_days"], ndays):
        day_tr = by_day.get(D, [])
        if not day_tr:
            continue
        m = Model.fit(trades, D, p)
        ok = []
        for t in day_tr:
            take, sc = m.approve(t["strategy"], t["f"])
            if take:
                ok.append(dict(t, score=sc))
        lt, bt_ = bt.portfolio(ok, cfg), bt.portfolio(day_tr, cfg)
        taken += lt
        base += bt_
        rows.append({"date": day_tr[0]["date"], "signals": len(day_tr), "approved": len(ok),
                     "learner_trades": len(lt), "learner_net": round(sum(t["net"] for t in lt)),
                     "every_signal_net": round(sum(t["net"] for t in bt_))})
    return taken, base, rows


def run(bars_by_sym, cfg, out_path, interval=5, p=None, log=print, names=None, nse=None):
    """Nightly job: fit on everything, walk-forward check, lessons, what changed since last night.
    `nse`: official.Context. With it, the walk-forward test is also run without the NSE facts."""
    p = dict(PARAMS, **(p or {}))
    names = list(names or cfg["strategies"])
    trades, days = labelled_trades(bars_by_sym, names, cfg, interval, nse)
    model = Model.fit(trades, len(days), p)
    prev = Model.load(out_path)
    taken, base, rows = walk_forward(trades, len(days), cfg, p)
    ls, bs = bt.stats(taken), bt.stats(base)
    with_nse = sum(1 for t in trades if any(k in t["f"] for k in NSE_VALUES))
    plain = None
    if with_nse:  # the same test with the NSE facts taken away: does NSE's data help?
        plain = bt.stats(walk_forward([dict(t, f={k: v for k, v in t["f"].items() if k in VALUES}) for t in trades],
                                      len(days), cfg, p)[0])

    total = model.total_setups()
    same = prev is not None and prev.facts() == model.facts()
    per = {}
    for name in names:
        s = model.d["strategies"].get(name, {"n": 0, "raw_r": 0.0, "r": 0.0})
        now_n = model.allowed_setups(name)
        was_n = prev.allowed_setups(name) if same else None
        per[name] = {"trades": s["n"], "avg_r": s["raw_r"], "shrunk_r": s["r"], "setups_allowed": now_n,
                     "setups_total": total, "setups_allowed_yesterday": was_n}
    changes = []
    for name, v in per.items():
        if v["setups_allowed_yesterday"] is not None and v["setups_allowed_yesterday"] != v["setups_allowed"]:
            changes.append("%s: now takes %d of %d setups (was %d)" % (
                name, v["setups_allowed"], total, v["setups_allowed_yesterday"]))
    if prev is None:
        changes.append("First run: model built from %d days of history." % len(days))
    elif not same:
        changes.append("The model now reads %d facts per trade (%s), so it tells %d setups apart; it read %d before." % (
            len(model.facts()), ", ".join(model.facts()), total, len(prev.facts())))
    elif not changes:
        changes.append("No change in what it would take tomorrow.")

    last = rows[-1] if rows else None
    out = {
        "asof": str(days[-1]) if days else None,
        "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "history_days": len(days),
        "first_day": str(days[0]) if days else None,
        "strategies": per,
        "lessons": model.lessons(),
        "changes": changes,
        "last_day": last,
        "nse": {"facts": [k for k in model.facts() if k in NSE_VALUES], "trades_with_nse_facts": with_nse,
                "trades": len(trades), "last_file": str(nse.days[-1]) if nse and nse.days else None},
        "walk_forward": {
            "days": len(rows),
            "learner": {k: ls.get(k) for k in ("trades", "win_rate", "expectancy_rs", "expectancy_r", "t_stat",
                                                "total_net", "max_drawdown")},
            "every_signal": {k: bs.get(k) for k in ("trades", "win_rate", "expectancy_rs", "expectancy_r",
                                                    "t_stat", "total_net", "max_drawdown")},
            "without_nse": {k: plain.get(k) for k in ("trades", "win_rate", "expectancy_rs", "expectancy_r", "t_stat",
                                                      "total_net", "max_drawdown")} if plain else None,
            "by_day": rows[-30:],
        },
        "model": model.d,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=1)

    log("Learned from %d days (%s to %s), %d trades across %d strategies." % (
        len(days), days[0], days[-1], len(trades), len(names)))
    log("\nWhat each strategy has earned (R per trade after costs; 'shrunk' is what the model believes):")
    for name, v in per.items():
        log("  %-9s %5d trades  avg %+.3f R  shrunk %+.3f R  setups it would take: %4d of %d" % (
            name, v["trades"], v["avg_r"], v["shrunk_r"], v["setups_allowed"], total))
    if nse:
        log("\nNSE's own daily file: %d of %d trades carry its facts (%s); latest file %s." % (
            with_nse, len(trades), ", ".join(out["nse"]["facts"]) or "none yet", out["nse"]["last_file"]))
    log("\nLessons (mistakes to avoid, and what did better):")
    for x in out["lessons"]:
        log("  - " + x)
    log("\nChanged since last night:")
    for x in changes:
        log("  - " + x)
    wf = out["walk_forward"]
    log("\nWalk-forward test (%d days, each judged by a model that had NOT seen it, 3 trades/day cap):" % wf["days"])
    for k, v in (("learner", wf["learner"]), ("without NSE", wf["without_nse"]), ("every signal", wf["every_signal"])):
        if v is None:
            continue
        if v.get("trades"):
            log("  %-13s %5d trades  win %5.1f%%  Rs %+7.1f a trade  total Rs %+9.0f  t %s" % (
                k, v["trades"], v["win_rate"], v["expectancy_rs"], v["total_net"], v["t_stat"]))
        else:
            log("  %-13s no trades (it found nothing worth taking)" % k)
    log("\nSaved %s" % os.path.relpath(out_path))
    return out
