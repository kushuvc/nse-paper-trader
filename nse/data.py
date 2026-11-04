"""Free NSE price data from Yahoo Finance (unofficial, can lag) with a local CSV cache.

Limits Yahoo imposes for .NS stocks: 5m bars ~60 days, 60m bars ~730 days,
daily bars back to the listing / late 1990s. `collect` appends to the cache,
so running it every evening grows a permanent 5-minute history.
"""
import csv
import datetime
import json
import os
import time
import urllib.error
import urllib.request

from .market import Bar, ist, now_ist, minutes, OPEN

UA = "Mozilla/5.0 (compatible; nse-day-trader/0.1)"
HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
PAUSE = 0.35
RANGES = {"5m": "60d", "15m": "60d", "60m": "730d", "1d": None}
NIFTY_CSV = "https://archives.nseindia.com/content/indices/ind_nifty%slist.csv"


class DataError(Exception):
    pass


def yahoo_symbol(sym):
    if sym.startswith("^") or sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return sym + ".NS"


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_chart(sym, interval, rng=None):
    """Raw bars from Yahoo. Returns list[Bar] (IST bar start times)."""
    ys = urllib.request.quote(yahoo_symbol(sym), safe="")
    if interval == "1d" and rng is None:
        q = "period1=0&period2=%d&interval=1d" % int(time.time())
    else:
        q = "range=%s&interval=%s" % (rng or RANGES[interval], interval)
    last = None
    for attempt in range(2):
        for host in HOSTS:
            try:
                doc = json.loads(_get("%s/v8/finance/chart/%s?%s" % (host, ys, q)))
                return parse_chart(doc)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404:
                    raise DataError("%s: not found on Yahoo" % sym)
            except (urllib.error.URLError, ValueError, OSError) as e:
                last = e
        time.sleep(5)
    raise DataError("%s: %s" % (sym, last))


def parse_chart(doc):
    ch = doc.get("chart") or {}
    if ch.get("error"):
        raise DataError(str(ch["error"].get("description") or ch["error"]))
    res = (ch.get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        return []
    q = res["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(res["timestamp"]):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        out.append(Bar(ist(ts), float(o), float(h), float(l), float(c), int(v or 0)))
    return out


def closed_only(bars, interval_min, now=None):
    """Drop the bar still forming and any off-grid 'live' tick Yahoo appends."""
    now = now or now_ist()
    out = []
    for b in bars:
        if (minutes(b.t) - minutes(OPEN)) % interval_min:
            continue
        if b.t.timestamp() + interval_min * 60 > now.timestamp():
            continue
        out.append(b)
    return out


# ------------------------------------------------------------------ cache
def cache_path(root, interval, sym):
    return os.path.join(root, interval, sym.replace("^", "IDX_").replace("&", "_AND_") + ".csv")


def load(root, interval, sym):
    p = cache_path(root, interval, sym)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for row in csv.reader(f):
            if row and row[0] != "ts":
                out.append(Bar(ist(int(row[0])), float(row[1]), float(row[2]), float(row[3]),
                               float(row[4]), int(row[5])))
    return out


def save(root, interval, sym, bars):
    p = cache_path(root, interval, sym)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "o", "h", "l", "c", "v"])
        for b in bars:
            w.writerow([int(b.t.timestamp()), b.o, b.h, b.l, b.c, b.v])
    os.replace(tmp, p)


def merge(old, new):
    by = {int(b.t.timestamp()): b for b in old}
    by.update({int(b.t.timestamp()): b for b in new})
    return [by[k] for k in sorted(by)]


def collect(root, interval, symbols, log=print):
    """Download and merge into the cache. Today's unfinished session is never saved."""
    iv = 1440 if interval == "1d" else int(interval[:-1])
    today = now_ist().date()
    ok = 0
    for i, s in enumerate(symbols):
        try:
            bars = fetch_chart(s, interval)
            if interval != "1d":
                bars = closed_only(bars, iv)
            done = now_ist().time() >= datetime.time(15, 35)
            bars = [b for b in bars if b.t.date() < today or done]
            merged = merge(load(root, interval, s), bars)
            save(root, interval, s, merged)
            ok += 1
            log("  [%d/%d] %-12s %6d bars  %s .. %s" % (i + 1, len(symbols), s, len(merged),
                                                      merged[0].t.date() if merged else "-",
                                                      merged[-1].t.date() if merged else "-"))
        except DataError as e:
            log("  [%d/%d] %-12s skipped: %s" % (i + 1, len(symbols), s, e))
        time.sleep(PAUSE)
    return ok


def nifty_list(n=50, root=None):
    """Current index constituents from NSE's own CSV, cached; falls back to the cache."""
    cache = os.path.join(root, "nifty%d.txt" % n) if root else None
    try:
        text = _get(NIFTY_CSV % n, timeout=15)
        syms = [r["Symbol"].strip() for r in csv.DictReader(text.splitlines()) if r.get("Symbol")]
        if len(syms) >= n * 0.9:
            if cache:
                os.makedirs(root, exist_ok=True)
                with open(cache, "w") as f:
                    f.write("\n".join(syms) + "\n")
            return syms
    except (urllib.error.URLError, OSError, ValueError):
        pass
    if cache and os.path.exists(cache):
        return [l.strip() for l in open(cache) if l.strip()]
    raise DataError("could not read the Nifty %d list from NSE and no cached copy" % n)
