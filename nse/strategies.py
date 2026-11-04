"""Intraday setups for NSE stocks.

Every strategy is a function scan(bars, ctx) -> Signal | None where `bars` are
today's CLOSED bars so far and ctx holds yesterday's close/high/low and the
bar interval. It returns a Signal only if the trigger happened on the LAST
bar: entry is that bar's close. The backtest walks the day bar by bar and the
live paper engine calls it on each new bar, so both see exactly the same
information. Nothing here can peek at the future.

The parameters below were chosen BEFORE looking at any results (classic
textbook versions), on purpose: tuning them to the data is how backtests lie.
"""
from collections import namedtuple

from .market import OPEN, minutes, round_tick, TICK

Signal = namedtuple("Signal", "side entry stop target note")  # side +1 long, -1 short


def _elapsed(bar, interval):
    """Minutes from 9:15 to the END of this bar."""
    return minutes(bar.t) - minutes(OPEN) + interval


def _opening_range(bars, or_min, interval):
    rng = [b for b in bars if _elapsed(b, interval) <= or_min]
    if len(rng) * interval < or_min:
        return None
    return max(b.h for b in rng), min(b.l for b in rng), rng


def _plan(side, entry, stop, target_r=None, target=None, note=""):
    risk = (entry - stop) * side
    if risk <= max(TICK, entry * 0.0005):  # too tight to survive spread/noise
        return None
    if target is None:
        target = entry + side * target_r * risk
    if (target - entry) * side <= 0:
        return None
    return Signal(side, round(entry, 2), round_tick(stop, up=side < 0), round_tick(target), note)


# ------------------------------------------------------------------ setups
def orb(or_min=15, stop_rule="mid", target_r=1.0):
    """Opening-range breakout: first close outside the first `or_min` minutes' range."""
    def scan(bars, ctx):
        iv = ctx["interval"]
        r = _opening_range(bars, or_min, iv)
        if not r or _elapsed(bars[-1], iv) <= or_min:
            return None
        hi, lo, rng = r
        after = bars[len(rng):]
        if any(b.c > hi or b.c < lo for b in after[:-1]):
            return None  # only the FIRST breakout of the day
        b = after[-1]
        mid = (hi + lo) / 2
        if b.c > hi:
            return _plan(+1, b.c, mid if stop_rule == "mid" else lo, target_r, note="ORB%d up" % or_min)
        if b.c < lo:
            return _plan(-1, b.c, mid if stop_rule == "mid" else hi, target_r, note="ORB%d down" % or_min)
        return None
    return scan


def gap_go(or_min=15, gap_pct=1.0, target_r=2.0):
    """Gap of >= gap_pct that keeps going in the first `or_min` minutes: join it."""
    def scan(bars, ctx):
        iv = ctx["interval"]
        if _elapsed(bars[-1], iv) != or_min or not ctx.get("prev_close"):
            return None
        o, pc = bars[0].o, ctx["prev_close"]
        gap = (o - pc) / pc * 100
        hi, lo = max(b.h for b in bars), min(b.l for b in bars)
        c = bars[-1].c
        if gap >= gap_pct and c > o:
            return _plan(+1, c, lo, target_r, note="gap +%.1f%% and go" % gap)
        if gap <= -gap_pct and c < o:
            return _plan(-1, c, hi, target_r, note="gap %.1f%% and go" % gap)
        return None
    return scan


def gap_fade(or_min=15, gap_pct=1.0, min_r=1.0):
    """Gap of >= gap_pct that reverses in the first `or_min` minutes: bet on filling it."""
    def scan(bars, ctx):
        iv = ctx["interval"]
        if _elapsed(bars[-1], iv) != or_min or not ctx.get("prev_close"):
            return None
        o, pc = bars[0].o, ctx["prev_close"]
        gap = (o - pc) / pc * 100
        hi, lo = max(b.h for b in bars), min(b.l for b in bars)
        c = bars[-1].c
        if gap >= gap_pct and c < o:
            p = _plan(-1, c, hi + TICK, target=pc, note="gap +%.1f%% fading" % gap)
        elif gap <= -gap_pct and c > o:
            p = _plan(+1, c, lo - TICK, target=pc, note="gap %.1f%% fading" % gap)
        else:
            return None
        if p and abs(p.target - p.entry) < min_r * abs(p.entry - p.stop):
            return None
        return p
    return scan


def vwap_reclaim(start="10:15", below_bars=3, target_r=2.0):
    """Trend day pullback: price that is up on the day closes back above VWAP after
    `below_bars` closes under it (mirror for shorts)."""
    sh, sm = map(int, start.split(":"))

    def scan(bars, ctx):
        if minutes(bars[-1].t) < sh * 60 + sm or len(bars) < below_bars + 2:
            return None
        pv = vol = 0.0
        vw = []
        for b in bars:
            tp = (b.h + b.l + b.c) / 3
            pv += tp * max(b.v, 1)
            vol += max(b.v, 1)
            vw.append(pv / vol)
        o = bars[0].o
        last, prev = bars[-1], bars[-1 - below_bars:-1]
        under = all(b.c < v for b, v in zip(prev, vw[-1 - below_bars:-1]))
        over = all(b.c > v for b, v in zip(prev, vw[-1 - below_bars:-1]))
        swing = bars[-1 - below_bars:]
        if under and last.c > vw[-1] and last.c > o:
            return _plan(+1, last.c, min(b.l for b in swing), target_r, note="VWAP reclaim")
        if over and last.c < vw[-1] and last.c < o:
            return _plan(-1, last.c, max(b.h for b in swing), target_r, note="VWAP loss")
        return None
    return scan


def high_winrate_trap(or_min=15, target_pct=0.15, stop_pct=1.5):
    """THE 90% ILLUSION, included as a warning, not a strategy.
    Buy after a green first 15 min; tiny target, huge stop. It wins most
    trades and still loses money: one stop wipes out ten wins, then costs
    take the rest."""
    def scan(bars, ctx):
        iv = ctx["interval"]
        if _elapsed(bars[-1], iv) != or_min or bars[-1].c <= bars[0].o:
            return None
        c = bars[-1].c
        return _plan(+1, c, c * (1 - stop_pct / 100), target=c * (1 + target_pct / 100),
                     note="tiny target, wide stop")
    return scan


# name -> (factory, intervals it makes sense on, one-line description)
REGISTRY = {
    "orb15":      (lambda: orb(15, "mid", 1.0), (5, 15), "15-min opening-range breakout, stop at range mid, 1R target"),
    "orb30_2r":   (lambda: orb(30, "far", 2.0), (5, 15), "30-min opening-range breakout, stop at other side, 2R target"),
    "orb60":      (lambda: orb(60, "mid", 1.0), (5, 15, 60), "first-hour range breakout, stop at mid, 1R"),
    "gap_go":     (lambda: gap_go(15, 1.0, 2.0), (5, 15), "gap >=1% that continues: join, 2R"),
    "gap_fade":   (lambda: gap_fade(15, 1.0), (5, 15), "gap >=1% that reverses: target yesterday's close"),
    "gap_go60":   (lambda: gap_go(60, 1.0, 2.0), (60,), "gap >=1% continuing after first hour, 2R"),
    "gap_fade60": (lambda: gap_fade(60, 1.0), (60,), "gap >=1% reversing after first hour"),
    "vwap":       (lambda: vwap_reclaim("10:15", 3, 2.0), (5,), "VWAP reclaim/loss with the day's trend, 2R"),
    "trap90":     (lambda: high_winrate_trap(), (5, 15), "WARNING demo: +0.15% target / -1.5% stop"),
}


def get(name):
    return REGISTRY[name][0]()
