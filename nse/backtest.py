"""Honest backtester: every stock, every full session, Indian costs, stop before target."""
import datetime
import math

from . import costs, risk, strategies
from .market import full_sessions, minutes


def bar_end(b, interval):
    return (datetime.datetime.combine(b.t.date(), b.t.time()) + datetime.timedelta(minutes=interval)).time()


def exit_check(sig, bar, interval, square_off):
    """Shared by backtest and paper engine. Returns (price, reason) or None.
    If one bar touches stop and target, the STOP wins (conservative)."""
    side = sig.side
    if side > 0:
        if bar.o <= sig.stop:
            return bar.o, "stop (gap)"
        if bar.l <= sig.stop:
            return sig.stop, "stop"
        if bar.o >= sig.target:
            return bar.o, "target"
        if bar.h >= sig.target:
            return sig.target, "target"
    else:
        if bar.o >= sig.stop:
            return bar.o, "stop (gap)"
        if bar.h >= sig.stop:
            return sig.stop, "stop"
        if bar.o <= sig.target:
            return bar.o, "target"
        if bar.l <= sig.target:
            return sig.target, "target"
    if bar_end(bar, interval) >= square_off:
        return bar.c, "square-off"
    return None


def run_session(scan, session, ctx, cfg):
    """First qualifying signal of the day -> one finished trade dict, or None."""
    iv = ctx["interval"]
    sq = risk.hhmm(cfg["square_off"])
    for i in range(len(session)):
        t = bar_end(session[i], iv)
        if t >= sq:
            return None
        sig = scan(session[:i + 1], ctx)
        if not sig:
            continue
        ok, _ = risk.can_enter(t, sig.side, 0, 0, cfg)
        if not ok:
            if t > risk.hhmm(cfg["no_entries_after"]):
                return None
            continue
        qty = risk.size(sig.entry, sig.stop, cfg)
        if qty <= 0:
            return None
        for b in session[i + 1:]:
            hit = exit_check(sig, b, iv, sq)
            if hit:
                return finish(sig, qty, hit[0], hit[1], session[i], b, cfg)
        return finish(sig, qty, session[-1].c, "end of data", session[i], session[-1], cfg)
    return None


def finish(sig, qty, px, reason, entry_bar, exit_bar, cfg):
    net, gross, ch = costs.round_trip(sig.side, qty, sig.entry, px, cfg.get("costs"))
    risk_rs = abs(sig.entry - sig.stop) * qty
    return {
        "date": str(entry_bar.t.date()), "time": entry_bar.t.strftime("%H:%M"), "side": sig.side,
        "entry": sig.entry, "stop": sig.stop, "target": sig.target, "exit": round(px, 2),
        "exit_time": exit_bar.t.strftime("%H:%M"), "reason": reason, "qty": qty, "note": sig.note,
        "gross": gross, "charges": ch, "net": net, "risk_rs": round(risk_rs, 2),
        "r": round(net / risk_rs, 3) if risk_rs else 0.0,
    }


def backtest(bars_by_sym, name, interval, cfg):
    scan = strategies.get(name)
    trades = []
    for sym, bars in bars_by_sym.items():
        sessions = full_sessions(bars, interval)
        for k in range(1, len(sessions)):
            prev = sessions[k - 1][1]
            ctx = {"interval": interval, "prev_close": prev[-1].c,
                   "prev_high": max(b.h for b in prev), "prev_low": min(b.l for b in prev)}
            t = run_session(scan, sessions[k][1], ctx, cfg)
            if t:
                t["symbol"] = sym
                trades.append(t)
    trades.sort(key=lambda t: (t["date"], t["time"], t["symbol"]))
    return trades


def stats(trades):
    n = len(trades)
    if not n:
        return {"trades": 0}
    nets = [t["net"] for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    eq = peak = dd = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    gross_pos = sum(t["gross"] for t in trades if t["gross"] > 0)
    rs = [t["r"] for t in trades]
    mean_r = sum(rs) / n
    sd_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    half = n // 2
    return {
        "trades": n,
        "days": len({t["date"] for t in trades}),
        "win_rate": round(100 * len(wins) / n, 1),
        "gross_win_rate": round(100 * sum(t["gross"] > 0 for t in trades) / n, 1),
        "gross_r": round(sum(t["gross"] / t["risk_rs"] for t in trades if t["risk_rs"]) / n, 3),
        "avg_win": round(avg_w, 0),
        "avg_loss": round(avg_l, 0),
        "breakeven_win_rate": round(100 * -avg_l / (avg_w - avg_l), 1) if wins and losses else None,
        "expectancy_rs": round(sum(nets) / n, 1),
        "expectancy_r": round(mean_r, 3),
        "t_stat": round(mean_r / (sd_r / math.sqrt(n)), 2) if sd_r else None,
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) else None,
        "total_net": round(sum(nets), 0),
        "total_charges": round(sum(t["charges"] for t in trades), 0),
        "charges_vs_gross_wins_pct": round(100 * sum(t["charges"] for t in trades) / gross_pos, 1) if gross_pos else None,
        "max_drawdown": round(dd, 0),
        "first_half_exp_r": round(sum(rs[:half]) / half, 3) if half else None,
        "second_half_exp_r": round(sum(rs[half:]) / (n - half), 3) if n - half else None,
    }


# ------------------------------------------------------------------ 20+ years on daily bars
def daily_rules(bars, rule, gap_pct=1.0, cfg=None):
    """Open-to-close trades on daily bars (no intraday stop: daily bars can't say what came first).
    rule: 'gap_fade', 'gap_go', 'buy_open'. Returns list of (date, side, entry, exit)."""
    out = []
    for prev, b in zip(bars, bars[1:]):
        if prev.c <= 0 or b.o <= 0:
            continue
        gap = (b.o - prev.c) / prev.c * 100
        if rule == "buy_open":
            side = 1
        elif abs(gap) < gap_pct:
            continue
        elif rule == "gap_fade":
            side = -1 if gap > 0 else 1
        else:
            side = 1 if gap > 0 else -1
        out.append((b.t.date(), side, b.o, b.c))
    return out


def daily_stats(trade_lists, notional=100000, cfg=None):
    rets = []
    by_year = {}
    for d, side, e, x in trade_lists:
        qty = max(1, int(notional // e))
        net, gross, ch = costs.round_trip(side, qty, e, x, (cfg or {}).get("costs"))
        r = net / (qty * e) * 100
        rets.append(r)
        by_year.setdefault(d.year, []).append(r)
    n = len(rets)
    if not n:
        return {"trades": 0}
    m = sum(rets) / n
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (n - 1)) if n > 1 else 0
    return {
        "trades": n, "win_rate": round(100 * sum(r > 0 for r in rets) / n, 1),
        "avg_net_pct": round(m, 4), "t_stat": round(m / (sd / math.sqrt(n)), 2) if sd else None,
        "years_positive": "%d/%d" % (sum(1 for y in by_year.values() if sum(y) > 0), len(by_year)),
        "by_year": {y: round(sum(v) / len(v), 3) for y, v in sorted(by_year.items())},
    }


def portfolio(trades, cfg):
    """What the paper account would actually take: first N signals per day by time,
    one position per stock, stop for the day after the daily loss limit."""
    taken, day, count, closed, busy = [], None, 0, [], {}
    for t in sorted(trades, key=lambda t: (t["date"], t["time"], t["symbol"])):
        if t["date"] != day:
            day, count, closed, busy = t["date"], 0, [], {}
        pnl = sum(net for ext, net in closed if ext <= t["time"])  # only trades already closed
        if count >= cfg["max_trades_per_day"] or pnl <= -abs(cfg["max_daily_loss"]):
            continue
        if busy.get(t["symbol"], "") > t["time"]:
            continue
        taken.append(t)
        count += 1
        closed.append((t["exit_time"], t["net"]))
        busy[t["symbol"]] = t["exit_time"]
    return taken
