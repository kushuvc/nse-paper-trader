"""Paper trading: dummy trades on live NSE prices. No broker, no real orders, ever.

Every 5 minutes (just after each bar closes) it:
  1. pulls the latest closed 5-min bars for the watchlist,
  2. checks open paper positions for stop / target / 15:15 square-off,
  3. runs the strategies on each stock's bars so far (same code as the backtest),
  4. opens a paper position when one triggers and the risk rules allow it.
Everything is written to paper/state-<date>.json and paper/journal.csv.
"""
import csv
import datetime
import json
import os
import time

from . import costs, data, learn, risk, strategies
from .backtest import exit_check, bar_end
from .market import IST, now_ist, split_sessions

INTERVAL = 5
JOURNAL_FIELDS = ["date", "symbol", "strategy", "side", "qty", "entry_time", "entry", "stop", "target",
                  "exit_time", "exit", "reason", "gross", "charges", "net", "r", "mode", "via", "setup"]
LEARNER = "learner"


class Engine:
    def __init__(self, cfg, symbols, out_dir, bars_fn=None, mode="live", log=print, model=None, nse=None):
        self.cfg, self.symbols, self.out, self.mode, self.log = cfg, symbols, out_dir, mode, log
        self.nse = nse      # official.Context: yesterday's facts from NSE's own file, for the learner (None = none)
        self.bars_fn = bars_fn or self._live_bars
        self.scans = {n: strategies.get(n) for n in cfg["strategies"]}
        self.model = model  # learn.Model: drives the extra "learner" account (None = no learner)
        self.state = None

    # ---------------------------------------------------------- state
    def _path(self, day):
        return os.path.join(self.out, "state-%s%s.json" % (day, "" if self.mode == "live" else "-" + self.mode))

    def _load(self, day):
        p = self._path(day)
        if os.path.exists(p):
            with open(p) as f:
                self.state = json.load(f)
            self.state["open"] = {(k if "|" in k else "%s|%s" % (v.get("strategy", "?"), k)): dict(v, symbol=v.get("symbol", k))
                                  for k, v in self.state["open"].items()}
        else:
            self.state = {"date": str(day), "open": {}, "closed": [], "seen": {}, "log": []}
        self.state.setdefault("considered", [])

    def _save(self):
        os.makedirs(self.out, exist_ok=True)
        with open(self._path(self.state["date"]), "w") as f:
            json.dump(self.state, f, indent=1)

    def say(self, now, msg):
        line = "%s  %s" % (now.strftime("%H:%M"), msg)
        self.state["log"].append(line)
        self.log(line)

    # ---------------------------------------------------------- data
    def _live_bars(self, sym, now):
        bars = data.fetch_chart(sym, "5m", "5d")
        time.sleep(data.PAUSE)
        return data.closed_only(bars, INTERVAL, now)

    # ---------------------------------------------------------- one step
    def step(self, now):
        day = now.date()
        if not self.state or self.state["date"] != str(day):
            self._load(day)
        st, cfg = self.state, self.cfg
        sq = risk.hhmm(cfg["square_off"])
        got = {}
        for sym in self.symbols:  # fetch everything first: the learner needs the whole market's move
            try:
                sess = split_sessions(self.bars_fn(sym, now))
            except data.DataError as e:
                self.say(now, "%s: no data (%s)" % (sym, e))
                continue
            if sess and sess[-1][0] == day:  # else holiday, or no bar closed yet today
                got[sym] = sess
        mkt = learn.market_moves({s: v[-1][1] for s, v in got.items()}) if self.model else {}
        for sym in self.symbols:
            if sym not in got:
                continue
            sess = got[sym]
            today = sess[-1][1]
            prev = sess[-2][1] if len(sess) > 1 else None
            last_seen = st["seen"].get(sym)
            new = [b for b in today if last_seen is None or b.t.strftime("%H:%M") > last_seen]
            if not new:
                continue
            st["seen"][sym] = today[-1].t.strftime("%H:%M")

            # 1) manage this stock's open positions (one per strategy) bar by bar
            for key, pos in list(st["open"].items()):
                if pos["symbol"] != sym:
                    continue
                sig = strategies.Signal(pos["side"], pos["entry"], pos["stop"], pos["target"], "")
                for b in new:
                    hit = exit_check(sig, b, INTERVAL, sq)
                    if hit:
                        self._close(key, hit[0], hit[1], b, now)
                        break
            if not prev:
                continue
            # 2) entries: every strategy is its own paper account (own trade cap and daily loss limit).
            #    Only the newest bar can trigger (never chase a stale signal).
            ctx = {"interval": INTERVAL, "prev_close": prev[-1].c, "prev_date": sess[-2][0],
                   "prev_high": max(b.h for b in prev), "prev_low": min(b.l for b in prev)}
            t_end = bar_end(today[-1], INTERVAL)
            for name, scan in self.scans.items():
                s = scan(today, ctx)
                if not s:
                    continue
                if self.model:
                    self._learner(name, sym, s, today, ctx, mkt, t_end, sq, now)
                key = "%s|%s" % (name, sym)
                if key in st["open"] or any(t["strategy"] == name and t["symbol"] == sym for t in st["closed"]):
                    continue  # one trade per stock per strategy per day
                mine = [t for t in st["closed"] if t["strategy"] == name]
                n_open = sum(1 for p in st["open"].values() if p["strategy"] == name)
                ok, why = risk.can_enter(t_end, s.side, len(mine) + n_open, sum(t["net"] for t in mine), cfg)
                if t_end >= sq:
                    ok, why = False, "past square-off"
                qty = risk.size(s.entry, s.stop, cfg) if ok else 0
                if ok and qty <= 0:
                    ok, why = False, "size works out to 0 shares"
                if not ok:
                    self.say(now, "SKIP  %-11s %-9s %s: %s" % (sym, name, s.note, why))
                    continue
                st["open"][key] = {"strategy": name, "symbol": sym, "side": s.side, "qty": qty, "entry": s.entry,
                                   "stop": s.stop, "target": s.target, "entry_time": t_end.strftime("%H:%M"),
                                   "note": s.note}
                self.say(now, "OPEN  %-11s %-5s %4d @ %.2f  stop %.2f  target %.2f  [%s: %s]" % (
                    sym, "BUY" if s.side > 0 else "SHORT", qty, s.entry, s.stop, s.target, name, s.note))
        # 3) square off anything still open after 15:15
        if now.time() >= sq:
            last_by_sym = {}
            for key, pos in list(st["open"].items()):
                sym = pos["symbol"]
                if sym not in last_by_sym:
                    try:
                        last_by_sym[sym] = [b for b in self.bars_fn(sym, now) if b.t.date() == day]
                    except data.DataError:
                        last_by_sym[sym] = []
                if last_by_sym[sym]:
                    bar = last_by_sym[sym][-1]
                    self._close(key, bar.c, "square-off", bar, now)
        self._save()

    def _learner(self, name, sym, s, today, ctx, mkt, t_end, sq, now):
        """The learner account: sees every strategy's FIRST in-window signal per stock (exactly the trades
        it was trained on) and takes it only if last night's model expects a profit after costs."""
        st, cfg = self.state, self.cfg
        tag = "%s|%s" % (name, sym)
        if tag in st["considered"] or not risk.can_enter(t_end, s.side, 0, 0, cfg)[0] or t_end >= sq:
            return
        st["considered"].append(tag)
        f = learn.features(s.side, s.entry, s.stop, today, ctx["prev_close"], mkt.get(today[-1].t.strftime("%H:%M")),
                           INTERVAL, self.nse.before(sym, ctx["prev_date"]) if self.nse else None)
        take, score = self.model.approve(name, f)
        if not take:
            return
        key = "%s|%s" % (LEARNER, sym)
        mine = [t for t in st["closed"] if t["strategy"] == LEARNER]
        n_open = sum(1 for p in st["open"].values() if p["strategy"] == LEARNER)
        ok, why = risk.can_enter(t_end, s.side, len(mine) + n_open, sum(t["net"] for t in mine), cfg)
        if key in st["open"]:
            ok, why = False, "already holding this stock"
        qty = risk.size(s.entry, s.stop, cfg) if ok else 0
        if ok and qty <= 0:
            ok, why = False, "size works out to 0 shares"
        if not ok:
            self.say(now, "SKIP  %-11s learner   via %s (expects %+.2fR): %s" % (sym, name, score, why))
            return
        st["open"][key] = {"strategy": LEARNER, "symbol": sym, "side": s.side, "qty": qty, "entry": s.entry,
                           "stop": s.stop, "target": s.target, "entry_time": t_end.strftime("%H:%M"),
                           "note": s.note, "via": name, "setup": learn.describe(f), "score": score}
        self.say(now, "OPEN  %-11s %-5s %4d @ %.2f  stop %.2f  target %.2f  [learner via %s, expects %+.2fR: %s]" % (
            sym, "BUY" if s.side > 0 else "SHORT", qty, s.entry, s.stop, s.target, name, score, learn.describe(f)))

    def _close(self, key, px, reason, bar, now):
        pos = self.state["open"][key]
        net, gross, ch = costs.round_trip(pos["side"], pos["qty"], pos["entry"], px, self.cfg.get("costs"))
        risk_rs = abs(pos["entry"] - pos["stop"]) * pos["qty"]
        rec = dict(pos, date=self.state["date"], exit=round(px, 2),
                   exit_time=bar_end(bar, INTERVAL).strftime("%H:%M"), reason=reason, gross=gross,
                   charges=ch, net=net, r=round(net / risk_rs, 3) if risk_rs else 0, mode=self.mode)
        self.state["closed"].append(rec)
        del self.state["open"][key]
        self.say(now, "CLOSE %-11s %-10s @ %.2f  net Rs %+.0f [%s] (charges %.0f)" % (
            pos["symbol"], reason, px, net, pos["strategy"], ch))
        self._journal(rec)

    def _journal(self, rec):
        os.makedirs(self.out, exist_ok=True)
        p = os.path.join(self.out, "journal.csv")
        new = not os.path.exists(p)
        if not new:  # older journals lack newer columns: rewrite once with the full header
            with open(p, newline="") as f:
                r = csv.DictReader(f)
                rows, head = list(r), r.fieldnames
            if head != JOURNAL_FIELDS:
                with open(p, "w", newline="") as f:
                    w = csv.DictWriter(f, JOURNAL_FIELDS, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, JOURNAL_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(rec)

    def summary(self):
        st = self.state
        if not st:
            return "no session"
        net = sum(t["net"] for t in st["closed"])
        wins = sum(t["net"] > 0 for t in st["closed"])
        lines = ["%s: %d closed (%d won), %d still open, net Rs %+.0f after charges" % (
            st["date"], len(st["closed"]), wins, len(st["open"]), net)]
        for name in list(self.scans) + ([LEARNER] if self.model else []):
            mine = [t for t in st["closed"] if t["strategy"] == name]
            lines.append("  %-9s %d trades, net Rs %+.0f" % (name, len(mine), sum(t["net"] for t in mine)))
        return "\n".join(lines)


def run_live(engine, until=datetime.time(15, 20)):
    """Loop through today's session. Wakes 20 s after each 5-min bar closes."""
    while True:
        now = now_ist()
        if now.weekday() >= 5:
            engine.log("Weekend: NSE is closed.")
            return
        if now.time() >= until:
            engine.step(now)
            engine.log(engine.summary())
            return
        if now.time() >= datetime.time(9, 20, 20):
            engine.step(now)
        nxt = now.replace(second=20, microsecond=0) + datetime.timedelta(minutes=5 - now.minute % 5)
        time.sleep(max(5, (nxt - now_ist()).total_seconds()))


def replay(engine, bars_by_sym, day):
    """Re-run a past day as if live, bar by bar, from saved bars. For testing the engine."""
    def provider(sym, now):
        return [b for b in bars_by_sym.get(sym, [])
                if b.t.date() <= day and b.t.timestamp() + INTERVAL * 60 <= now.timestamp()]
    engine.bars_fn = provider
    t = datetime.datetime.combine(day, datetime.time(9, 20, 20), IST)
    end = datetime.datetime.combine(day, datetime.time(15, 20, 20), IST)
    while t <= end:
        engine.step(t)
        t += datetime.timedelta(minutes=5)
    engine.log(engine.summary())
