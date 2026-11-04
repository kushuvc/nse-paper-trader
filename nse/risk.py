"""Position sizing and the day's risk limits (the NSE version of trader-budget + trader-risk)."""
import datetime
import json
import os

DEFAULTS = {
    "capital": 100000,             # Rs, paper account
    "risk_per_trade": 500,         # Rs lost if the stop is hit (0.5 % of capital)
    "max_position_pct": 100,       # no leverage by default (MIS offers ~5x: not used)
    "max_trades_per_day": 3,
    "max_daily_loss": 1500,        # stop trading for the day after this realised loss
    "no_entries_before": "09:30",
    "no_entries_after": "14:30",
    "square_off": "15:15",         # brokers auto-square MIS ~15:15-15:25
    "allow_short": True,           # intraday shorting is allowed in the NSE cash segment (MIS)
    "strategies": ["orb15"],
    "universe": "nifty50",
    "interval": "5m",
    "costs": {},
}


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


def hhmm(s):
    h, m = s.split(":")
    return datetime.time(int(h), int(m))


def size(entry, stop, cfg):
    """Shares so that a stop-out loses about risk_per_trade, capped by capital."""
    per_share = abs(entry - stop)
    if per_share <= 0:
        return 0
    qty = int(cfg["risk_per_trade"] // per_share)
    cap = int(cfg["capital"] * cfg["max_position_pct"] / 100 // entry)
    return max(0, min(qty, cap))


def can_enter(t, side, trades_today, pnl_today, cfg):
    """(ok, reason). t is the IST time the order would go in."""
    if side < 0 and not cfg["allow_short"]:
        return False, "shorts are off"
    if t < hhmm(cfg["no_entries_before"]):
        return False, "before %s" % cfg["no_entries_before"]
    if t > hhmm(cfg["no_entries_after"]):
        return False, "after %s" % cfg["no_entries_after"]
    if trades_today >= cfg["max_trades_per_day"]:
        return False, "max %d trades a day" % cfg["max_trades_per_day"]
    if pnl_today <= -abs(cfg["max_daily_loss"]):
        return False, "daily loss limit hit"
    return True, ""
