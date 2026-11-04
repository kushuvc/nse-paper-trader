"""Indian intraday (MIS) equity charges on NSE, discount-broker style (Zerodha/Upstox/Groww-like).

Rates as published by discount brokers in 2025-26. Check your own broker's
contract note: change them in config.json -> "costs" if yours differ.
"""
DEFAULT = {
    "brokerage_pct": 0.03,        # per executed order ...
    "brokerage_cap": 20.0,        # ... or Rs 20, whichever is lower
    "stt_sell_pct": 0.025,        # Securities Transaction Tax, sell side only (intraday)
    "exchange_pct": 0.00297,      # NSE transaction charge, both sides
    "sebi_per_crore": 10.0,       # SEBI turnover fee, both sides
    "stamp_buy_pct": 0.003,       # stamp duty, buy side only
    "gst_pct": 18.0,              # on brokerage + exchange + SEBI fees
    "slippage_pct": 0.02,         # assumed fill worse than the bar price, EACH side
}


def charges(buy_value, sell_value, c=None):
    """Statutory + broker charges in rupees for one buy and one sell leg (no slippage)."""
    c = dict(DEFAULT, **(c or {}))
    brok = sum(min(v * c["brokerage_pct"] / 100, c["brokerage_cap"]) for v in (buy_value, sell_value) if v > 0)
    turnover = buy_value + sell_value
    stt = sell_value * c["stt_sell_pct"] / 100
    exch = turnover * c["exchange_pct"] / 100
    sebi = turnover * c["sebi_per_crore"] / 1e7
    stamp = buy_value * c["stamp_buy_pct"] / 100
    gst = (brok + exch + sebi) * c["gst_pct"] / 100
    return round(brok + stt + exch + sebi + stamp + gst, 2)


def slip(price, side, leg, c=None):
    """Price after slippage. side +1 long / -1 short; leg 'entry' or 'exit'. Always against you."""
    c = dict(DEFAULT, **(c or {}))
    s = c["slippage_pct"] / 100
    worse_up = (side > 0) == (leg == "entry")  # long entry / short exit pay more
    return price * (1 + s) if worse_up else price * (1 - s)


def round_trip(side, qty, entry, exit_, c=None):
    """Net rupee P&L of a trade after slippage and every charge. Returns (net, gross, charges)."""
    e = slip(entry, side, "entry", c)
    x = slip(exit_, side, "exit", c)
    if side > 0:
        buy, sell = e * qty, x * qty
    else:
        buy, sell = x * qty, e * qty
    gross = (sell - buy)
    ch = charges(buy, sell, c)
    return round(gross - ch, 2), round(gross, 2), ch
