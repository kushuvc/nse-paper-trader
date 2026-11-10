"""Round 3: does holding for days or weeks (swing trading) work where same-day trading did not?

Protocol, written on 10 Nov 2026 before any of these ideas had been run on any real prices:

1. WHAT IS TRADED. Shares are bought and paid for in full (delivery) and held overnight. Buying
   only: the cash market does not allow a short sale to be carried overnight. No borrowed money.
   A Rs 1 lakh account holds at most 5 positions of equal size. Every decision is made at the day's
   close and filled at that close, 0.05% worse than the closing price on each side. Only closing
   prices and volumes are used, because the free daily opens, highs and lows are unreliable.
   Stops are therefore checked on closes too.

2. CHARGES. Today's delivery charges (costs.DELIVERY: about 0.40% for a round trip of Rs 20,000
   including slippage) are applied to every year, because the question is whether an idea pays now.

3. WHICH STOCKS, without knowing the future. Candidates are today's Nifty 500 plus FORMER, a list
   of once heavily traded stocks that have since left it (where free data still exists). At each
   month-end close the 50 candidates with the highest median daily traded value over the previous
   60 trading days become the universe for the following month. A stock needs 260 earlier trading
   days. So a stock that was big then and is small now is in the test for the months it was big.
   Bias that remains: companies whose prices are no longer published at all are missing, which
   flatters every idea here, and most of all the ones that buy falling stocks.

4. DATA RULES. A bar with no volume is dropped. A one-day move of 40% or more is not trusted (it is
   usually a split or bonus the data missed): the stock cannot enter the universe or be bought for
   the next 252 trading days. If a trade is open on such a day, a fall is counted in full and a
   rise is not counted at all (the trade ends at the previous close). The benchmark does the
   opposite: it counts such a rise in full and leaves such a fall out. These choices can make an
   idea look worse, never better. (Checked on the design years before any idea was run: 9 such
   moves in 13 years among universe stocks, all of them real events.)

5. TWO PERIODS.
     design years   decisions from 1 Jan 2010 to 31 Dec 2022   every idea is run here first
     exam years     decisions from 1 Jan 2023 onwards          not looked at while choosing
   The design run is given no price after 31 Dec 2022; trades still open then are closed at that
   day's close. The exam account starts empty on 1 Jan 2023.
   What was already known about the exam years when this was written (from Round 2, hourly bars):
   the average Nifty 50 stock rose about 44% from Nov 2023 to Nov 2026, all of it overnight, and
   2026 was about flat. No swing idea had been run on any period.

6. THE MEASURE. For each trade: the result after charges, in % of the money put in, and two ways
   of simply holding over the same days, neither paying any charge:
     the benchmark   the 50 universe stocks held in equal amounts
     the index       the Nifty 50 index itself (prices, no dividends; the stocks have none either)
   Excess = trade after charges - benchmark. An idea that only rides a rising market has no
   excess, on purpose: an index fund does that without any of this. The index is there as well
   because it has no missing companies, and because the data check made before any idea was run
   showed the benchmark to be the weaker of the two in the design years (7.6% a year against 10.0%).

7. An idea goes to the exam only if, in the design years, its average trade made money after
   charges AND its average excess was above zero AND it also beat the index on average.

8. It passes the exam only if, in the exam years, all three of those hold again AND the
   t-statistic of the excess, taken month by month of entry, is EXAM_MIN_T or more. By month, not
   by trade: positions overlap and stocks move together. 2.5, not the usual 2, because six ideas
   are tried.

9. Passing earns a paper account only. It would then have to pass every rule of the real-money
   gate (nse/gate.py) and, in addition, beat holding the universe over its paper days, because a
   buy-only account can pass the gate on a rising market alone. That adds a rule; none is loosened.

10. Every idea is reported, including the failures, with one extra column that is information and
    not a pass rule: the result if every buy and sell is made one day late.

11. THE SIX IDEAS (IDEAS below). Each has a reason to exist and round-number settings. There is no
    search over settings and no second version of an idea after its result is seen.
"""
import array
import bisect
import collections
import datetime
import math

from . import costs

REGISTERED = "2026-11-10"
DESIGN_FROM = datetime.date(2010, 1, 1)
SPLIT = datetime.date(2023, 1, 1)
EXAM_MIN_T = 2.5
UNIVERSE = 50          # stocks in the universe
LIQ_DAYS = 60          # traded value is judged over this many earlier days
MIN_HISTORY = 260      # earlier trading days a stock needs
SLOTS = 5              # positions held at most
POSITION = 20000.0     # rupees per position when working out charges
BAD_MOVE = 0.40        # a one-day move this large is not trusted
BAD_DAYS = 252         # ... and keeps the stock out for this many trading days
HIGH_DAYS = 250        # "new high" means above the closes of this many earlier days
LOAD_FROM = datetime.date(2007, 1, 1)

# Once among the most traded shares on the NSE, since dropped from the Nifty 500, merged or failed.
# Written from memory before any result, successes and failures alike; whether one enters the
# universe in a given month is decided by the traded-value rule above, not by this list. Names the
# free data no longer has are skipped. Names still in the Nifty 500 are harmless duplicates.
FORMER = """UNITECH RCOM JPASSOCIAT HDIL RELCAPITAL DHFL JETAIRWAYS LITL IVRCLINFRA EDUCOMP KFA PUNJLLOYD
VIDEOIND GTLINFRA GTL IBREALEST RELINFRA RPOWER JPPOWER JPINFRATEC BHUSANSTL ESSAROIL RANBAXY CAIRN SSLT
SESAGOA STER HDFC IDFC MINDTREE ABAN RENUKA BAJAJHIND IFCI HCC FRETAIL PANTALOONR DISHTV JISLJALEQS
OPTOCIRCUI SATYAMCOMP GVKPIL KSOILS ORCHIDCHEM SINTEX HOTELEELA DECCANCHR 3IINFOTECH ROLTA AMTEKAUTO
ALOKTEXT ALOKINDS ISPATIND RNRL ABGSHIP PIPAVAVDOC RNAVAL COX&KINGS MANPASAND PCJEWELLER VAKRANGEE
KWALITY IL&FSTRANS RELIGARE SREINFRA MCLEODRUSS SUZLON YESBANK IDEA ADANIPOWER GMRINFRA EKC PRAJIND
HINDOILEXP BALRAMCHIN TRIVENI NAGARFERT CHAMBLFERT ESSARSHPNG ESSARPORTS MERCATOR GESHIP SCI WELCORP
JINDALSAW MONNETISPA ADHUNIK ELECTCAST BGRENERGY MOSERBAER TULIP SKUMARSYNF KOUTONS ZYLOG CORE EVERONN
ARSSINFRA SUJANATWR SUBEX FINANTECH 63MOONS TTML MTNL IBULHSGFIN INFRATEL ALBK ANDHRABANK SYNDIBANK
ORIENTBANK CORPBANK VIJAYABANK DENABANK LAKSHVILAS INGVYSYABK SOUTHBANK TATAMTRDVR TV18BRDCST NETWORK18
HEXAWARE POLARIS PATNI IGATE NIITTECH HCL-INSYS MASTEK GITANJALI DEN HATHWAY JUSTDIAL IDFCBANK LTI
ADLABS RELMEDIA BOMDYEING CENTURYTEX SPICEJET DLF RAJESHEXPO JPVL LANCOIN NCC NAGARCONST GAMMONIND
PATELENG SIMPLEXINF ERAINFRA MADHUCON STRTECH TATATELE TANLA GLODYNE ICSA SPICEMOBI ONMOBILE EDELWEISS
INDIAINFO IIFL JMFINANCIL MOTILALOFS KARURVYSYA DCB DEWANHOUS BHARTISHIP VARUNSHIP TATAMOTORS""".split()

_DATES = {}


def candidates(nifty500):
    """Today's Nifty 500 plus the former big names, without repeats."""
    return list(dict.fromkeys(list(nifty500) + FORMER))


class Series:
    """One stock's daily closes and volumes after the data rules, with running sums for averages.
    Everything here is indexed by the stock's own trading days; index k means 'that day's close'."""
    __slots__ = ("sym", "d", "ix", "c", "v", "r", "bad", "last_bad", "newhigh", "_pc", "_pv", "_pr", "_pr2")

    def __init__(self, sym, bars, upto=None, since=LOAD_FROM):
        seen = {}
        for b in bars:
            day = b.t.date()
            if b.c > 0 and b.v > 0 and day >= since and (upto is None or day < upto):
                seen[_DATES.setdefault(day, day)] = (b.c, b.v)
        self.sym = sym
        self.d = sorted(seen)
        self.ix = {day: k for k, day in enumerate(self.d)}
        c = self.c = array.array("d", (seen[x][0] for x in self.d))
        self.v = array.array("d", (seen[x][1] for x in self.d))
        n = len(c)
        r = self.r = array.array("d", [0.0]) + array.array("d", (c[k] / c[k - 1] - 1 for k in range(1, n)))
        self.bad = [abs(x) >= BAD_MOVE for x in r]
        self.last_bad, last = array.array("l"), -10 ** 6
        for k in range(n):
            if self.bad[k]:
                last = k
            self.last_bad.append(last)
        self._pc, self._pv = _running(c), _running(self.v)
        self._pr, self._pr2 = _running(r), _running(x * x for x in r)
        self.newhigh, best = [], collections.deque()  # indexes of earlier closes, highest first
        for k in range(n):
            while best and best[0] < k - HIGH_DAYS:
                best.popleft()
            self.newhigh.append(k >= HIGH_DAYS and c[k] > c[best[0]])
            while best and c[best[-1]] <= c[k]:
                best.pop()
            best.append(k)

    def clean(self, k):
        """No untrusted move in the last BAD_DAYS trading days, today included."""
        return k - self.last_bad[k] >= BAD_DAYS

    def sma(self, k, n):
        """Average of the last n closes, today included."""
        return (self._pc[k + 1] - self._pc[k + 1 - n]) / n if k + 1 >= n else None

    def vavg(self, k, n):
        """Average volume of the n days before today."""
        return (self._pv[k] - self._pv[k - n]) / n if k >= n else None

    def stdev(self, k, n):
        """How much the price moved day to day over the last n days, today included."""
        if k < n:
            return None
        m = (self._pr[k + 1] - self._pr[k + 1 - n]) / n
        var = (self._pr2[k + 1] - self._pr2[k + 1 - n]) / n - m * m
        return math.sqrt(max(var, 0.0) * n / (n - 1))

    def upto(self, day):
        """Index of the last bar on or before `day` (-1 if none)."""
        return bisect.bisect_right(self.d, day) - 1


def _running(xs):
    out, tot = array.array("d", [0.0]), 0.0
    for x in xs:
        tot += x
        out.append(tot)
    return out


class Market:
    """The trading calendar, the month-by-month universe and its equal-weighted benchmark."""

    def __init__(self, series, universe=UNIVERSE, index=None):
        self.s = series
        self.index = sorted(index.items()) if index else []   # [(date, Nifty 50 close)]
        self._index_days = [d for d, _ in self.index]
        count = collections.Counter()
        for s in series.values():
            count.update(s.d)
        need = max(1, min(20, len(series) // 2))
        cal = self.cal = sorted(d for d, n in count.items() if n >= need)
        self.cix = {d: i for i, d in enumerate(cal)}
        # known in advance from the exchange's holiday list, so not a look into the future:
        self.month_end = {i for i in range(len(cal) - 1) if cal[i + 1].month != cal[i].month}
        self.week_end = {i for i in range(len(cal) - 1)
                         if tuple(cal[i + 1].isocalendar())[:2] != tuple(cal[i].isocalendar())[:2]}
        self.universes, self.uni = [], []
        for i, d in enumerate(cal):
            if i in self.month_end:
                self.universes.append(self._rank(d, universe))
            self.uni.append(len(self.universes) - 1)
        self.cum, self.untrusted = array.array("d", [0.0]), []
        for i in range(1, len(cal)):
            tot, cnt = 0.0, 0
            for sym in self.universe(i - 1):
                s = series[sym]
                k = s.ix.get(cal[i])
                if k is None:          # did not trade today: no change
                    cnt += 1
                    continue
                if s.bad[k]:
                    self.untrusted.append((cal[i], sym, round(s.r[k] * 100, 1)))
                    if s.r[k] < 0:     # untrusted fall: left out, which keeps the benchmark high
                        continue
                tot += s.r[k]
                cnt += 1
            self.cum.append(self.cum[-1] + (math.log(1 + tot / cnt) if cnt else 0.0))

    def _rank(self, day, size):
        rows = []
        for sym, s in self.s.items():
            k = s.ix.get(day)
            if k is None or k < MIN_HISTORY or not s.clean(k):
                continue
            tv = sorted(s.c[j] * s.v[j] for j in range(k - LIQ_DAYS, k))
            rows.append((-(tv[LIQ_DAYS // 2 - 1] + tv[LIQ_DAYS // 2]) / 2, sym))
        rows.sort()
        return {sym: n for n, (_, sym) in enumerate(rows[:size])}

    def universe(self, i):
        """{symbol: rank} in force for decisions at the close of calendar day i (0 = most traded)."""
        return self.universes[self.uni[i]] if i is not None and self.uni[i] >= 0 else {}

    def day(self, date):
        """Calendar index of the last trading day on or before `date`."""
        return max(bisect.bisect_right(self.cal, date) - 1, 0)

    def held(self, d0, d1):
        """What the universe, held in equal amounts, returned from the close of d0 to the close of d1."""
        return math.exp(self.cum[self.day(d1)] - self.cum[self.day(d0)]) - 1

    def index_held(self, d0, d1):
        """What the Nifty 50 index returned from the close of d0 to the close of d1 (None without index data)."""
        a, b = bisect.bisect_right(self._index_days, d0) - 1, bisect.bisect_right(self._index_days, d1) - 1
        return self.index[b][1] / self.index[a][1] - 1 if a >= 0 else None


def build(symbols, load, upto=None, universe=UNIVERSE, index=None):
    """Market from `load(symbol) -> daily bars`. With `upto`, no bar on or after that date is kept,
    for the stocks or for the index (`index`: the Nifty 50's own daily bars)."""
    series = {}
    for sym in symbols:
        s = Series(sym, load(sym), upto)
        if len(s.d) > MIN_HISTORY:
            series[sym] = s
    idx = {b.t.date(): b.c for b in index or () if b.c > 0 and (upto is None or b.t.date() < upto)}
    return Market(series, universe, idx)


# ------------------------------------------------------------------ the six ideas
# A score or signal for index k may read index k and earlier only.
def _momentum(s, k):
    return s.c[k - 21] / s.c[k - 252] - 1 if k >= 252 else None


def _low_vol(s, k):
    sd = s.stdev(k, 60)
    return -sd if sd is not None else None


def _losers(s, k):
    return -(s.c[k] / s.c[k - 5] - 1) if k >= 5 else None


def _high52_in(s, k):
    return k >= HIGH_DAYS + 20 and s.newhigh[k] and not any(s.newhigh[k - 20:k])


def _high52_out(s, ke, j):
    return j - ke >= 20 or s.c[j] <= 0.92 * s.c[ke]


def _pullback_in(s, k):
    c = s.c
    return k >= 200 and c[k] > s.sma(k, 200) and c[k] < c[k - 1] < c[k - 2] < c[k - 3] and c[k] / c[k - 3] - 1 <= -0.05


def _pullback_out(s, ke, j):
    return j - ke >= 10 or s.c[j] > s.sma(j, 5)


def _surge_in(s, k):
    return k >= 21 and s.r[k] >= 0.04 and s.v[k] >= 3 * s.vavg(k, 20)


def _surge_out(s, ke, j):
    return j - ke >= 10 or s.c[j] <= 0.92 * s.c[ke]


IDEAS = collections.OrderedDict([
    ("momentum", {"when": "month", "score": _momentum, "text":
        "At each month end hold the 5 stocks that rose most over the 11 months ending a month ago. "
        "Why: winners of the past year have tended to keep winning for some months."}),
    ("low_vol", {"when": "month", "score": _low_vol, "text":
        "At each month end hold the 5 stocks whose price moved least from day to day over the last 60 days. "
        "Why: steady stocks have tended to return more than their risk suggests."}),
    ("high52", {"enter": _high52_in, "leave": _high52_out, "text":
        "Buy on the first close above every close of the past 250 days (after 20 days without one). "
        "Sell after 20 trading days, or earlier on a close 8% below the entry. Why: a new one-year high is news that "
        "holders anchored to the old high are slow to price."}),
    ("pullback", {"enter": _pullback_in, "leave": _pullback_out, "text":
        "In a stock above its 200-day average, buy after 3 lower closes in a row that add up to a fall of 5% or more. "
        "Sell on the first close above the 5-day average, or after 10 trading days. Why: sharp dips inside an uptrend "
        "have tended to be bought back."}),
    ("losers", {"when": "week", "score": _losers, "text":
        "At each week end hold the 5 stocks that fell most over the last 5 trading days, for one week. "
        "Why: the past week's biggest losers have tended to bounce the week after."}),
    ("volume_surge", {"enter": _surge_in, "leave": _surge_out, "text":
        "Buy at the close of a day the stock rose 4% or more on at least 3 times its usual volume. Sell after 10 "
        "trading days, or earlier on a close 8% below the entry. Why: big news is absorbed over days, not in one."}),
])

Trade = collections.namedtuple("Trade", "sym ke kx why rank")  # ke/kx: the stock's own day indexes of entry and exit


def _rotate(mkt, idea, start, end, slots):
    """Ideas that re-pick their holdings on a schedule: a stock is held while it stays picked."""
    days = mkt.month_end if idea["when"] == "month" else mkt.week_end
    held, trades = {}, []
    for i in range(bisect.bisect_left(mkt.cal, start), bisect.bisect_right(mkt.cal, end)):
        d = mkt.cal[i]
        for sym in list(held):
            s = mkt.s[sym]
            k = s.ix.get(d)
            if k is not None and s.bad[k] and s.r[k] > 0:  # untrusted jump: out at the close before it
                ke, rank = held.pop(sym)
                trades.append(Trade(sym, ke, k - 1, "jump", rank))
        if i not in days:
            continue
        scored = []
        for sym, rank in mkt.universe(i).items():
            s = mkt.s[sym]
            k = s.ix.get(d)
            if k is None or not s.clean(k):
                continue
            sc = idea["score"](s, k)
            if sc is not None:
                scored.append((-sc, rank, sym))
        scored.sort()
        picks = [sym for _, _, sym in scored[:slots]]
        for sym in list(held):
            if sym not in picks:
                ke, rank = held.pop(sym)
                trades.append(Trade(sym, ke, mkt.s[sym].upto(d), "rule", rank))
        for n, sym in enumerate(picks):
            if sym not in held:
                held[sym] = (mkt.s[sym].ix[d], n)
    for sym, (ke, rank) in held.items():
        kx = mkt.s[sym].upto(end)
        if kx > ke:
            trades.append(Trade(sym, ke, kx, "end", rank))
    return trades


def _events(mkt, idea, start, end):
    """Ideas that buy on a signal and sell by their own rule. One open trade per stock at a time."""
    enter, leave, trades = idea["enter"], idea["leave"], []
    for sym, s in mkt.s.items():
        k, n = bisect.bisect_left(s.d, start), bisect.bisect_right(s.d, end)
        while k < n:
            i = mkt.cix.get(s.d[k])
            rank = mkt.universe(i).get(sym)
            if rank is None or not s.clean(k) or not enter(s, k):
                k += 1
                continue
            ke, kx, why = k, n - 1, "end"
            for j in range(ke + 1, n):
                if s.bad[j] and s.r[j] > 0:  # untrusted jump: out at the close before it
                    kx, why = j - 1, "jump"
                    break
                if leave(s, ke, j):
                    kx, why = j, "rule"
                    break
            if kx > ke or why == "jump":
                trades.append(Trade(sym, ke, kx, why, rank))
            k = max(kx, ke) + 1
    trades.sort(key=lambda t: (mkt.s[t.sym].d[t.ke], t.rank))
    return trades


def simulate(mkt, name, start, end, slots=SLOTS):
    """Every trade the idea makes with decisions from `start` to `end` (dates, inclusive)."""
    idea = IDEAS[name]
    return _rotate(mkt, idea, start, end, slots) if "score" in idea else _events(mkt, idea, start, end)


def price(mkt, trades, delay=0, cost=None):
    """Each trade's result in %, and what holding the universe did over the same days.
    delay=1 fills every buy and sell one trading day after it was decided."""
    rows = []
    for t in trades:
        s = mkt.s[t.sym]
        last = len(s.c) - 1
        fe = min(t.ke + delay, last)
        fx = t.kx if t.why == "jump" else min(t.kx + delay, last)
        if fx < fe or (fx == fe and (delay or t.why != "jump")):
            continue
        net, gross = costs.delivery_pct(s.c[fe], s.c[fx], POSITION, cost)
        bench = mkt.held(s.d[fe], s.d[fx]) * 100
        idx = mkt.index_held(s.d[fe], s.d[fx])
        rows.append({"symbol": t.sym, "entry_date": str(s.d[fe]), "exit_date": str(s.d[fx]),
                     "days": mkt.day(s.d[fx]) - mkt.day(s.d[fe]), "gross_pct": gross, "net_pct": net,
                     "bench_pct": bench, "excess_pct": net - bench,
                     "vs_index_pct": None if idx is None else net - idx * 100, "why": t.why})
    return rows


def _t(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return round(m / (sd / math.sqrt(n)), 2) if sd else None


def summary(rows):
    n = len(rows)
    if not n:
        return {"trades": 0}

    def avg(key, rs=rows):
        return sum(r[key] for r in rs) / len(rs)

    months, years = {}, {}
    for r in rows:
        months.setdefault(r["entry_date"][:7], []).append(r["excess_pct"])
        years.setdefault(r["entry_date"][:4], []).append(r)
    return {
        "trades": n, "months": len(months),
        "avg_days": round(avg("days"), 1),
        "win_rate": round(100 * sum(r["net_pct"] > 0 for r in rows) / n, 1),
        "beat_holding": round(100 * sum(r["excess_pct"] > 0 for r in rows) / n, 1),
        "gross_pct": round(avg("gross_pct"), 3), "net_pct": round(avg("net_pct"), 3),
        "bench_pct": round(avg("bench_pct"), 3), "excess_pct": round(avg("excess_pct"), 3),
        "vs_index_pct": None if any(r.get("vs_index_pct") is None for r in rows) else round(avg("vs_index_pct"), 3),
        "t_excess_by_month": _t([sum(v) / len(v) for v in months.values()]),
        "by_year": {y: {"trades": len(rs), "net_pct": round(avg("net_pct", rs), 2),
                        "excess_pct": round(avg("excess_pct", rs), 2)} for y, rs in sorted(years.items())},
    }


def _curve(dates, values, start_value):
    peak, worst = start_value, 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9) if dates else 0
    final = values[-1] if values else start_value
    return {"final": round(final), "a_year_pct": round(((final / start_value) ** (1 / years) - 1) * 100, 1) if years else 0.0,
            "worst_fall_pct": round(worst * 100, 1)}


def account(mkt, trades, start, end, capital=100000.0, slots=SLOTS, cost=None):
    """Information only: a Rs 1 lakh account with `slots` equal positions taking the trades as they
    come (the most traded stock first when there are more signals than free slots)."""
    c = dict(costs.DELIVERY, **(cost or {}))
    slip = c["slippage_pct"] / 100
    by_entry = {}
    for t in trades:
        by_entry.setdefault(mkt.s[t.sym].d[t.ke], []).append(t)
    cash, open_, taken, invested, values = capital, {}, 0, 0.0, []
    lo, hi = bisect.bisect_left(mkt.cal, start), bisect.bisect_right(mkt.cal, end)
    for i in range(lo, hi):
        d = mkt.cal[i]
        for sym in list(open_):
            qty, t = open_[sym]
            s = mkt.s[sym]
            if s.d[t.kx] <= d:
                val = qty * s.c[t.kx] * (1 - slip)
                cash += val - costs.delivery_charges(0, val, c)
                del open_[sym]

        def stock_value():
            return sum(q * mkt.s[sym].c[mkt.s[sym].upto(d)] for sym, (q, _) in open_.items())

        for t in sorted(by_entry.get(d, ()), key=lambda t: t.rank):
            if len(open_) >= slots or t.sym in open_:
                continue
            size = min((cash + stock_value()) / slots, cash / 1.003)
            if size < 1000:
                continue
            buy = size * (1 + slip)
            cash -= buy + costs.delivery_charges(buy, 0, c)
            open_[t.sym] = (size / mkt.s[t.sym].c[t.ke], t)
            taken += 1
        sv = stock_value()
        values.append(cash + sv)
        invested += sv / (cash + sv)
    out = _curve(mkt.cal[lo:hi], values, capital)
    out.update(trades_taken=taken, invested_pct=round(100 * invested / max(len(values), 1), 1))
    return out


def holding(mkt, start, end, capital=100000.0):
    """The benchmark as an account: the universe held in equal amounts, no charges."""
    lo, hi = bisect.bisect_left(mkt.cal, start), bisect.bisect_right(mkt.cal, end)
    return _curve(mkt.cal[lo:hi], [capital * math.exp(mkt.cum[i] - mkt.cum[lo]) for i in range(lo, hi)], capital)


def index_account(mkt, start, end, capital=100000.0):
    """The Nifty 50 index itself (prices only, no dividends), for comparison."""
    pts = [(d, c) for d, c in mkt.index if start <= d <= end]
    if len(pts) < 2:
        return None
    return _curve([d for d, _ in pts], [capital * c / pts[0][1] for _, c in pts], capital)


def quality(mkt, start, end):
    """Facts about the data in a period. No idea is run here."""
    lo, hi = bisect.bisect_left(mkt.cal, start), bisect.bisect_right(mkt.cal, end)
    members = set()
    for u in sorted({mkt.uni[i] for i in range(lo, hi) if mkt.uni[i] >= 0}):
        members.update(mkt.universes[u])
    former = set(FORMER)
    sizes = [len(mkt.universe(i)) for i in range(lo, hi)]
    odd = [x for x in mkt.untrusted if start <= x[0] <= end]
    return {
        "from": str(mkt.cal[lo]) if hi > lo else None, "to": str(mkt.cal[hi - 1]) if hi > lo else None,
        "trading_days": hi - lo,
        "stocks_with_prices": sum(1 for s in mkt.s.values() if s.d[0] <= end and s.d[-1] >= start),
        "former_names_with_prices": sorted(sym for sym in mkt.s if sym in former),
        "stocks_ever_in_universe": len(members),
        "former_names_ever_in_universe": sorted(members & former),
        "smallest_universe": min(sizes) if sizes else 0,
        "untrusted_moves": [(str(d), sym, pct) for d, sym, pct in odd],
        "holding_universe": holding(mkt, start, end),
        "nifty50_index": index_account(mkt, start, end),
    }


# ------------------------------------------------------------------ the study
HEAD = "%-13s %6s %6s %6s %6s %8s %8s %8s %8s %8s %9s %9s" % (
    "idea", "trades", "months", "days", "win %", "before", "after", "holding", "excess", "t(month)", "vs index", "day late")


def qualifies(s):
    """Rule 7: money after charges, above the benchmark, above the index."""
    return bool(s["trades"]) and s["net_pct"] > 0 and s["excess_pct"] > 0 and (s["vs_index_pct"] or 0) > 0


def _line(name, s):
    if not s["trades"]:
        return "%-13s %6d" % (name, 0)
    late = s.get("day_late") or {}
    return "%-13s %6d %6d %6.1f %6.1f %+8.2f %+8.2f %+8.2f %+8.2f %8s %+9.2f %+9.2f" % (
        name, s["trades"], s["months"], s["avg_days"], s["win_rate"], s["gross_pct"], s["net_pct"],
        s["bench_pct"], s["excess_pct"], s["t_excess_by_month"],
        s["vs_index_pct"] if s["vs_index_pct"] is not None else float("nan"), late.get("excess_pct", float("nan")))


def _run(mkt, name, start, end):
    trades = simulate(mkt, name, start, end)
    s = summary(price(mkt, trades))
    if s["trades"]:
        late = summary(price(mkt, trades, delay=1))
        s["day_late"] = {k: late.get(k) for k in ("trades", "net_pct", "excess_pct")}
        s["account"] = account(mkt, trades, start, end)
    return s


def _accounts(log, period, res, names, q):
    log("\nRs 1,00,000 over the %s (information, not a pass rule):" % period)
    log("%-38s %12s %8s %11s %9s" % ("", "ends at Rs", "a year", "worst fall", "invested"))
    for n in names:
        a = (res[n] or {}).get("account")
        if a:
            log("%-38s %12d %7.1f%% %10.1f%% %8.0f%%" % (n, a["final"], a["a_year_pct"], a["worst_fall_pct"], a["invested_pct"]))
    for label, a in (("holding the universe, no charges", q["holding_universe"]),
                     ("Nifty 50 index, no dividends", q["nifty50_index"])):
        if a:
            log("%-38s %12d %7.1f%% %10.1f%% %8s" % (label, a["final"], a["a_year_pct"], a["worst_fall_pct"], "100%"))


def study(symbols, load, exam=False, index=None, log=print):
    """Design years for every idea; with exam=True, the exam years for the ideas that qualified."""
    names = list(IDEAS)
    res = {"registered": REGISTERED, "design_from": str(DESIGN_FROM), "split": str(SPLIT), "exam_min_t": EXAM_MIN_T,
           "exam_opened": False, "ideas": {n: IDEAS[n]["text"] for n in names}, "design": {}, "exam": {},
           "promoted": [], "passed": []}
    mkt = build(symbols, load, upto=SPLIT, index=index)
    d_end = SPLIT - datetime.timedelta(days=1)
    q = res["design_data"] = quality(mkt, DESIGN_FROM, d_end)
    log("DESIGN YEARS: decisions %s to %s, %d trading days. No later price is loaded." % (DESIGN_FROM, d_end, q["trading_days"]))
    log("%d stocks with prices, %d of them in the 50-stock universe at some point (%d of those have since left the Nifty 500)." % (
        q["stocks_with_prices"], q["stocks_ever_in_universe"], len(q["former_names_ever_in_universe"])))
    log("Per trade, in % of the money put in. before/after = before/after charges; holding = the universe over the same days;")
    log("excess = after - holding; t(month) = how sure the excess is; vs index = after - the Nifty 50 over the same days;")
    log("day late = excess if every order is a day late.\n")
    log(HEAD)
    for n in names:
        s = res["design"][n] = _run(mkt, n, DESIGN_FROM, d_end)
        if qualifies(s):
            res["promoted"].append(n)
        log(_line(n, s))
    _accounts(log, "design years", res["design"], names, q)
    log("\nMade money after charges AND beat both ways of holding in the design years, so allowed into the exam: %s" % (
        ", ".join(res["promoted"]) or "none"))
    if exam and res["promoted"]:
        del mkt
        mkt = build(symbols, load, index=index)
        res["exam_opened"] = True
        e_end = mkt.cal[-1]
        q = res["exam_data"] = quality(mkt, SPLIT, e_end)
        log("\nEXAM YEARS: decisions %s to %s, only for the ideas above." % (SPLIT, e_end))
        log("Pass = money after charges, excess above zero, above the index, and t(month) >= %g.\n" % EXAM_MIN_T)
        log(HEAD)
        for n in res["promoted"]:
            s = res["exam"][n] = _run(mkt, n, SPLIT, e_end)
            ok = qualifies(s) and (s["t_excess_by_month"] or 0) >= EXAM_MIN_T
            if ok:
                res["passed"].append(n)
            log(_line(n, s) + ("   PASS" if ok else "   fail"))
        _accounts(log, "exam years", res["exam"], res["promoted"], q)
        log("\nPassed the exam: %s" % (", ".join(res["passed"]) or "none"))
    return res
