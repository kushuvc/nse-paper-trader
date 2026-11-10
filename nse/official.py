"""NSE's own end-of-day files, saved once a day.

The 5-minute bars the bot trades on come from Yahoo, which is unofficial. Every evening (about
18:30 to 19:30 IST) NSE itself publishes one file with every share's official open, high, low and
close, its volume, the number of trades and the DELIVERY quantity: shares that changed hands for
keeps, as opposed to being bought and sold again the same day. A second file has every index's
close (Nifty 50, India VIX and the rest). Yahoo has no delivery numbers at all.

What is kept, small enough to live in the repo and grow for years:
  data/nse/days/YYYY-MM-DD.csv   the 500 most traded shares that day, plus the bot's watchlist
  data/nse/market.csv            one line per day about the whole market (how many shares rose,
                                 how much was taken for delivery, Nifty 50, India VIX)

Context turns those files into two facts the nightly learner may use, both known before the
market opens (nse/learn.py says how they are judged). One request per file per day, from NSE's
public archive. Nothing here can place an order.
"""
import csv
import datetime
import os
import time
import urllib.error
import urllib.request

from .data import DataError, UA
from .market import now_ist

HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
BHAV = "/products/content/sec_bhavdata_full_%s.csv"       # %s = DDMMYYYY
INDICES = "/content/indices/ind_close_all_%s.csv"
KEEP = 500                                                # most traded shares kept per day
PUBLISHED_BY = datetime.time(18, 0)                       # today's file is not looked for before this
PAUSE = 0.5
COLS = ["symbol", "prev_close", "open", "high", "low", "close", "vwap", "volume", "turnover_lacs", "trades",
        "deliv_qty", "deliv_pct"]
MARKET_COLS = ["date", "stocks", "advances", "declines", "unchanged", "adv_pct", "median_move_pct", "turnover_cr",
               "deliv_pct", "nifty", "nifty_chg_pct", "nifty_pe", "vix", "vix_chg_pct"]


class NoFile(Exception):
    """NSE has no file for that day: a holiday, a weekend, or not published yet."""


def download(path):
    """Text of one archive file. NoFile if NSE has none; DataError if NSE cannot be reached."""
    last = None
    for host in HOSTS:
        req = urllib.request.Request(host + path, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoFile(path)
            last = e
        except (urllib.error.URLError, OSError) as e:
            last = e
    raise DataError("NSE's archive could not be reached (%s)" % last)


def _num(x):
    try:
        return float((x or "").strip())
    except ValueError:
        return None


def _text(v):
    """A value as NSE gave it: nothing rounded away."""
    if v is None:
        return ""
    if isinstance(v, float):
        return "%d" % v if v == int(v) else repr(v)
    return v


def parse_bhav(text, day):
    """Rows for ordinary shares (series EQ) from NSE's full end-of-day file.
    NoFile if the file is for another day: on a holiday NSE serves the previous day's file again."""
    rows = []
    for r in csv.DictReader(text.splitlines(), skipinitialspace=True):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("SERIES") != "EQ":
            continue
        try:
            if datetime.datetime.strptime(r["DATE1"], "%d-%b-%Y").date() != day:
                raise NoFile("NSE's file is dated %s, not %s" % (r["DATE1"], day))
        except (KeyError, ValueError):
            raise DataError("NSE's file is not in the expected layout")
        row = {"symbol": r["SYMBOL"], "prev_close": _num(r.get("PREV_CLOSE")), "open": _num(r.get("OPEN_PRICE")),
               "high": _num(r.get("HIGH_PRICE")), "low": _num(r.get("LOW_PRICE")), "close": _num(r.get("CLOSE_PRICE")),
               "vwap": _num(r.get("AVG_PRICE")), "volume": _num(r.get("TTL_TRD_QNTY")),
               "turnover_lacs": _num(r.get("TURNOVER_LACS")), "trades": _num(r.get("NO_OF_TRADES")),
               "deliv_qty": _num(r.get("DELIV_QTY")), "deliv_pct": _num(r.get("DELIV_PER"))}
        if row["close"] and row["prev_close"] and row["volume"]:
            rows.append(row)
    if len(rows) < 500:
        raise DataError("NSE's file has only %d shares" % len(rows))
    return rows


def parse_indices(text):
    """{index name: {close, chg_pct, pe}} from NSE's index file."""
    out = {}
    for r in csv.DictReader(text.splitlines(), skipinitialspace=True):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("Index Name"):
            out[r["Index Name"]] = {"close": _num(r.get("Closing Index Value")), "chg_pct": _num(r.get("Change(%)")),
                                    "pe": _num(r.get("P/E"))}
    return out


def summarise(day, rows, indices=None):
    """One line about the whole market that day."""
    moves = sorted((r["close"] / r["prev_close"] - 1) * 100 for r in rows)
    adv, dec = sum(m > 0 for m in moves), sum(m < 0 for m in moves)
    n = len(moves)
    med = moves[n // 2] if n % 2 else (moves[n // 2 - 1] + moves[n // 2]) / 2
    known = [r for r in rows if r["deliv_qty"] is not None and r["vwap"]]
    traded = sum(r["volume"] * r["vwap"] for r in known)
    n50, vix = (indices or {}).get("Nifty 50", {}), (indices or {}).get("India VIX", {})
    return {"date": str(day), "stocks": n, "advances": adv, "declines": dec, "unchanged": n - adv - dec,
            "adv_pct": round(100.0 * adv / (adv + dec), 1) if adv + dec else None,
            "median_move_pct": round(med, 2),
            "turnover_cr": round(sum(r["turnover_lacs"] or 0 for r in rows) / 100),
            "deliv_pct": round(100.0 * sum(r["deliv_qty"] * r["vwap"] for r in known) / traded, 1) if traded else None,
            "nifty": n50.get("close"), "nifty_chg_pct": n50.get("chg_pct"), "nifty_pe": n50.get("pe"),
            "vix": vix.get("close"), "vix_chg_pct": vix.get("chg_pct")}


# ------------------------------------------------------------------ files
def _day_path(root, day):
    return os.path.join(root, "days", "%s.csv" % day)


def days(root):
    """Dates that have a saved file, oldest first."""
    d = os.path.join(root, "days")
    out = []
    for f in os.listdir(d) if os.path.isdir(d) else ():
        try:
            out.append(datetime.date.fromisoformat(f[:-4]))
        except ValueError:
            pass
    return sorted(out)


def save_day(root, day, rows, watch=()):
    keep = {r["symbol"] for r in sorted(rows, key=lambda r: -(r["turnover_lacs"] or 0))[:KEEP]} | set(watch)
    p = _day_path(root, day)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in sorted(rows, key=lambda r: r["symbol"]):
            if r["symbol"] in keep:
                w.writerow([_text(r[c]) for c in COLS])
    os.replace(p + ".tmp", p)


def load_day(root, day):
    """{symbol: row} for one saved day ({} if there is no file)."""
    p = _day_path(root, day)
    if not os.path.exists(p):
        return {}
    with open(p, newline="") as f:
        return {r["symbol"]: dict({c: _num(r.get(c)) for c in COLS[1:]}, symbol=r["symbol"]) for r in csv.DictReader(f)}


def load_market(root):
    p = os.path.join(root, "market.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="") as f:
        return {r["date"]: dict({c: _num(r.get(c)) for c in MARKET_COLS[1:]}, date=r["date"]) for r in csv.DictReader(f)}


def save_market(root, market):
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, "market.csv")
    with open(p + ".tmp", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(MARKET_COLS)
        for d in sorted(market):
            w.writerow(["" if market[d].get(c) is None else market[d][c] for c in MARKET_COLS])
    os.replace(p + ".tmp", p)


def collect(root, watch=(), back=7, now=None, fetch=download, log=print, pause=PAUSE):
    """Save any of the last `back` days' files that are missing. Returns the dates newly saved.
    Stops at the first sign that NSE cannot be reached, so a blocked run costs seconds, not minutes."""
    now = now or now_ist()
    have, market, new = set(days(root)), load_market(root), []
    for n in range(back, -1, -1):
        day = now.date() - datetime.timedelta(days=n)
        if day in have or (n == 0 and now.time() < PUBLISHED_BY):
            continue
        stamp = day.strftime("%d%m%Y")
        try:
            rows = parse_bhav(fetch(BHAV % stamp), day)
        except NoFile:
            if day.weekday() < 5:
                log("  %s: no file from NSE (a holiday, or not published yet)" % day)
            continue
        except DataError as e:
            log("  %s: %s. Will try again on the next run." % (day, e))
            break
        try:
            indices = parse_indices(fetch(INDICES % stamp))
        except (NoFile, DataError):
            indices = {}
        m = market[str(day)] = summarise(day, rows, indices)
        save_market(root, market)   # the market line first: if the run dies before the next line, the day is fetched again
        save_day(root, day, rows, watch)
        new.append(day)
        log("  %s: saved. %s" % (day, describe(m)))
        if pause:
            time.sleep(pause)
    return new


def describe(m):
    """The day in one plain sentence."""
    bits = ["%d of %d shares rose (%s%%)" % (m["advances"], m["stocks"], "%g" % m["adv_pct"] if m.get("adv_pct") is not None else "?")]
    if m.get("nifty"):
        bits.append("Nifty 50 %s%s" % ("{:,.0f}".format(m["nifty"]),
                                       " (%+.2f%%)" % m["nifty_chg_pct"] if m.get("nifty_chg_pct") is not None else ""))
    if m.get("deliv_pct") is not None:
        bits.append("%g%% of the money traded was taken for delivery" % m["deliv_pct"])
    if m.get("vix"):
        bits.append("India VIX %g" % m["vix"])
    return ", ".join(bits) + "."


# ------------------------------------------------------------------ what the learner may know before the open
class Context:
    """Numbers from the file of the session BEFORE the one being traded. Nothing from the trading day itself."""
    DELIVERY_DAYS = 20   # a share's delivery is compared with its own average over this many earlier days
    DELIVERY_MIN = 10    # ... and needs at least this many of them

    def __init__(self, root):
        self.root = root
        self.days = days(root)
        self._ix = {d: i for i, d in enumerate(self.days)}
        self.market = load_market(root)
        self._rows = {}

    def _day(self, day):
        if day not in self._rows:
            self._rows[day] = load_day(self.root, day)
        return self._rows[day]

    def before(self, symbol, prev_day):
        """{'deliv_ratio': ..., 'adv_pct': ...} from `prev_day`'s file; whatever is not known is left out.
        Empty if that day's file was never saved: an older file is not used in its place."""
        i = self._ix.get(prev_day)
        if i is None:
            return {}
        out = {}
        row = self._day(prev_day).get(symbol)
        past = [self._day(d).get(symbol) for d in self.days[max(0, i - self.DELIVERY_DAYS):i]]
        vals = [r["deliv_pct"] for r in past if r and r.get("deliv_pct")]
        if row and row.get("deliv_pct") and len(vals) >= self.DELIVERY_MIN:
            out["deliv_ratio"] = row["deliv_pct"] / (sum(vals) / len(vals))
        m = self.market.get(str(prev_day))
        if m and m.get("adv_pct") is not None:
            out["adv_pct"] = m["adv_pct"]
        return out
