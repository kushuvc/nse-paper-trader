#!/usr/bin/env python3
"""NSE day-trading research + paper trading. Standard-library Python 3.8+, nothing to install.

  python3 trade.py universe                          # Nifty 50 list from NSE
  python3 trade.py collect --interval 5m             # save/extend 5-min history (run every evening)
  python3 trade.py collect --interval 60m            # ~2 years of hourly bars
  python3 trade.py collect --interval 1d             # full daily history
  python3 trade.py backtest --interval 5m            # every strategy, every stock, after costs
  python3 trade.py backtest --interval 5m --strategy orb15 --trades
  python3 trade.py daily                             # 20+ year open-to-close gap study
  python3 trade.py learn                             # nightly: learn from mistakes, walk-forward check
  python3 trade.py replay --date 2026-11-04          # paper engine on a past day, bar by bar
  python3 trade.py paper                             # LIVE dummy trades today, 9:20-15:20 IST
  python3 trade.py report                            # paper journal so far
  python3 trade.py status                            # write docs/status.json for the phone page
"""
import argparse
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nse import backtest as bt, data, learn, paper, risk, strategies  # noqa: E402
from nse.market import IST, now_ist  # noqa: E402

DATA = os.path.join(HERE, "data")
PAPER = os.path.join(HERE, "paper")
RESULTS = os.path.join(HERE, "results")
MODEL = os.path.join(HERE, "model", "learned.json")


def symbols(cfg, a):
    if getattr(a, "symbols", None):
        return [s.strip().upper() for s in a.symbols.split(",")]
    n = 100 if cfg["universe"] == "nifty100" else 50
    return data.nifty_list(n, DATA)


def iv_min(interval):
    return int(interval[:-1])


def cmd_universe(cfg, a):
    s = symbols(cfg, a)
    print("%d symbols: %s" % (len(s), " ".join(s)))


def cmd_collect(cfg, a):
    syms = symbols(cfg, a) + (["^NSEI", "^NSEBANK"] if a.interval == "1d" else [])
    print("Collecting %s bars for %d symbols into data/%s/ ..." % (a.interval, len(syms), a.interval))
    ok = data.collect(DATA, a.interval, syms)
    print("Done: %d/%d saved." % (ok, len(syms)))


def load_all(cfg, a, interval):
    out = {}
    for s in symbols(cfg, a):
        b = data.load(DATA, interval, s)
        if b:
            out[s] = b
    if not out:
        sys.exit("No %s data yet. Run: python3 trade.py collect --interval %s" % (interval, interval))
    return out


def fmt(v):
    return "-" if v is None else str(v)


def cmd_backtest(cfg, a):
    iv = iv_min(a.interval)
    bars = load_all(cfg, a, a.interval)
    names = [a.strategy] if a.strategy else [n for n, (_, ivs, _) in strategies.REGISTRY.items() if iv in ivs]
    sess = {s: len(bt.full_sessions(b, iv)) for s, b in bars.items()}
    first = min(b[0].t.date() for b in bars.values())
    last = max(b[-1].t.date() for b in bars.values())
    print("Backtest on %d stocks, %s bars, %s to %s (~%d sessions each)." % (
        len(bars), a.interval, first, last, max(sess.values())))
    print("Sizing: Rs %s risk per trade, capital Rs %s, flat by %s. Costs: STT, exchange, SEBI, stamp, GST, "
          "brokerage, %.2f%% slippage each side.\n" % (cfg["risk_per_trade"], cfg["capital"], cfg["square_off"],
                                                       bt.costs.DEFAULT["slippage_pct"]))
    cols = ["trades", "gross_win_rate", "gross_r", "win_rate", "breakeven_win_rate", "avg_win", "avg_loss", "expectancy_rs", "expectancy_r",
            "t_stat", "profit_factor", "total_net", "total_charges", "max_drawdown", "first_half_exp_r",
            "second_half_exp_r"]
    hdr = ["strategy", "trades", "win% pre-cost", "R pre-cost", "win%", "need%", "avg win", "avg loss", "Rs/trade", "R/trade", "t",
           "PF", "total Rs", "charges", "max DD", "R 1st half", "R 2nd half"]
    rows, all_res = [], {}
    for n in names:
        tr = bt.backtest(bars, n, iv, cfg)
        if a.portfolio:
            tr = bt.portfolio(tr, cfg)
        st = bt.stats(tr)
        all_res[n] = {"stats": st, "trades": tr}
        rows.append([n] + [fmt(st.get(c)) for c in cols])
    w = [max(len(str(r[i])) for r in rows + [hdr]) for i in range(len(hdr))]
    print("  ".join(h.rjust(w[i]) for i, h in enumerate(hdr)))
    for r in rows:
        print("  ".join(str(x).rjust(w[i]) for i, x in enumerate(r)))
    print("\nwin% = trades that made money after costs. need% = win rate needed to break even with these avg win/loss.")
    print("t = t-statistic of R per trade: below ~2 means the result is indistinguishable from luck.")
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, "backtest-%s%s.json" % (a.interval, "-portfolio" if a.portfolio else ""))
    with open(out, "w") as f:
        json.dump({n: v["stats"] for n, v in all_res.items()}, f, indent=1)
    if a.trades and a.strategy:
        p = os.path.join(RESULTS, "trades-%s-%s.csv" % (a.strategy, a.interval))
        tr = all_res[a.strategy]["trades"]
        if tr:
            with open(p, "w", newline="") as f:
                wr = csv.DictWriter(f, list(tr[0].keys()))
                wr.writeheader()
                wr.writerows(tr)
            print("Trades: %s" % os.path.relpath(p))
    print("Saved: %s" % os.path.relpath(out))


def cmd_daily(cfg, a):
    bars = load_all(cfg, a, "1d")
    print("Daily open-to-close study, %d stocks, %s to %s. Rs 1 lakh per trade, full Indian costs.\n" % (
        len(bars), min(b[0].t.date() for b in bars.values()), max(b[-1].t.date() for b in bars.values())))
    print("%-24s %8s %6s %12s %7s %14s" % ("rule", "trades", "win%", "avg net %", "t", "years +ve"))
    res = {}
    for rule, g in (("buy_open (baseline)", None), ("gap_go (>=1%)", 1.0), ("gap_fade (>=1%)", 1.0),
                    ("gap_go (>=2%)", 2.0), ("gap_fade (>=2%)", 2.0)):
        key = rule.split()[0]
        tl = []
        for s, b in bars.items():
            tl += bt.daily_rules(b, key, g or 0)
        st = bt.daily_stats(tl, cfg=cfg)
        res[rule] = st
        print("%-24s %8d %6.1f %12.4f %7s %14s" % (rule, st["trades"], st["win_rate"], st["avg_net_pct"],
                                                  fmt(st["t_stat"]), st["years_positive"]))
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "daily-study.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    print("\nNo stops here: a daily bar cannot say whether the high or the low came first.")
    print("WARNING: do not trust a positive gap_fade line here. Yahoo's daily 'open' differs from the first")
    print("tradable price by >0.5% on ~7% of stock-days (checked against 5-min bars, Nov 2026), and the")
    print("'edge' shrinks from ~1%/trade before 2005 to ~0.06% after 2024. With real 5-min opens the")
    print("same rule LOST money over the last 60 days. A data artifact, not an edge.")


def cmd_learn(cfg, a):
    """Nightly: learn from all saved 5-min history (including today's mistakes)."""
    learn.run(load_all(cfg, a, "5m"), cfg, MODEL)


def cmd_replay(cfg, a):
    day = datetime.date.fromisoformat(a.date)
    bars = load_all(cfg, a, "5m")
    syms = list(bars)
    model = None
    if cfg.get("learner", True):  # honest: the learner only knows the days BEFORE the one replayed
        trades, days = learn.labelled_trades(bars, cfg["strategies"], cfg)
        model = learn.Model.fit(trades, sum(d < day for d in days))
    eng = paper.Engine(cfg, syms, PAPER, mode="replay", model=model)
    if os.path.exists(eng._path(day)):
        os.remove(eng._path(day))
    print("Replaying %s for %d stocks with %s%s ..." % (day, len(syms), ", ".join(cfg["strategies"]),
                                                       " + learner" if model else ""))
    paper.replay(eng, bars, day)


def cmd_paper(cfg, a):
    syms = symbols(cfg, a)
    print("PAPER TRADING (dummy money, no orders sent anywhere). Strategies: %s. %d stocks. Ctrl-C to stop."
          % (", ".join(cfg["strategies"]), len(syms)))
    model = learn.Model.load(MODEL) if cfg.get("learner", True) else None
    print("Learner: %s" % ("model from %s" % model.d.get("days") + " days of history" if model
                           else "off (no model/learned.json yet: run `python3 trade.py learn`)"))
    eng = paper.Engine(cfg, syms, PAPER, mode="live", model=model)
    if a.once:
        eng.step(now_ist())
        print(eng.summary())
    else:
        paper.run_live(eng, until=datetime.time(*map(int, a.until.split(":"))))


def cmd_status(cfg, a):
    """Write docs/status.json for the phone page (GitHub Pages)."""
    now = now_ist()
    day = now.date().isoformat()
    st = {}
    p = os.path.join(PAPER, "state-%s.json" % day)
    if os.path.exists(p):
        st = json.load(open(p))
    jp = os.path.join(PAPER, "journal.csv")
    journal = [r for r in csv.DictReader(open(jp)) if r.get("mode") == "live"] if os.path.exists(jp) else []
    nets = [float(r["net"]) for r in journal]
    closed = st.get("closed", [])
    in_session = now.weekday() < 5 and datetime.time(9, 10) <= now.time() <= datetime.time(15, 25)
    out = {
        "updated": now.strftime("%Y-%m-%d %H:%M IST"),
        "updated_ts": int(now.timestamp()),
        "in_session": in_session and a.running,
        "strategies": cfg["strategies"],
        "date": day,
        "open": st.get("open", {}),
        "closed": closed,
        "today_net": round(sum(t["net"] for t in closed), 0),
        "log": st.get("log", [])[-60:],
        "all_trades": len(nets),
        "all_wins": sum(x > 0 for x in nets),
        "all_net": round(sum(nets), 0),
        "recent": journal[-25:][::-1],
        "by_day": {},
    }
    for r in journal:
        out["by_day"][r["date"]] = round(out["by_day"].get(r["date"], 0) + float(r["net"]), 0)
    desc = {n: strategies.REGISTRY[n][2] for n in cfg["strategies"] if n in strategies.REGISTRY}
    by = {n: {"desc": desc.get(n, ""), "today_trades": 0, "today_net": 0.0, "open": 0,
              "all_trades": 0, "all_wins": 0, "all_net": 0.0} for n in cfg["strategies"]}
    for t in closed:
        b = by.setdefault(t["strategy"], {"desc": "", "today_trades": 0, "today_net": 0.0, "open": 0,
                                          "all_trades": 0, "all_wins": 0, "all_net": 0.0})
        b["today_trades"] += 1
        b["today_net"] += t["net"]
    for p_ in st.get("open", {}).values():
        if p_.get("strategy") in by:
            by[p_["strategy"]]["open"] += 1
    for r in journal:
        b = by.setdefault(r["strategy"], {"desc": "", "today_trades": 0, "today_net": 0.0, "open": 0,
                                          "all_trades": 0, "all_wins": 0, "all_net": 0.0})
        b["all_trades"] += 1
        b["all_wins"] += float(r["net"]) > 0
        b["all_net"] += float(r["net"])
    for b in by.values():
        b["today_net"], b["all_net"] = round(b["today_net"]), round(b["all_net"])
    if cfg.get("learner", True) and "learner" in by:
        by["learner"]["desc"] = "takes only the signals last night's model expects to profit"
    elif cfg.get("learner", True):
        by["learner"] = {"desc": "takes only the signals last night's model expects to profit", "today_trades": 0,
                         "today_net": 0, "open": 0, "all_trades": 0, "all_wins": 0, "all_net": 0}
    out["by_strategy"] = by
    if os.path.exists(MODEL):
        m = json.load(open(MODEL))
        out["learning"] = {k: m.get(k) for k in ("asof", "history_days", "lessons", "changes", "strategies",
                                                  "walk_forward", "last_day")}
        out["learning"]["walk_forward"] = dict(m["walk_forward"], by_day=m["walk_forward"]["by_day"][-10:])
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    with open(os.path.join(HERE, "docs", "status.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("docs/status.json updated (%d trades in journal)." % len(nets))


def cmd_report(cfg, a):
    p = os.path.join(PAPER, "journal.csv")
    if not os.path.exists(p):
        sys.exit("No paper trades yet.")
    rows = [r for r in csv.DictReader(open(p)) if a.all or r["mode"] == "live"]
    if not rows:
        sys.exit("No live paper trades yet (use --all to include replays).")
    for r in rows:
        print("%s %-11s %-9s %-5s %5s @ %9s -> %9s %-11s net Rs %8s" % (
            r["date"], r["symbol"], r["strategy"], "BUY" if r["side"] == "1" else "SHORT", r["qty"], r["entry"],
            r["exit"], r["reason"], r["net"]))
    nets = [float(r["net"]) for r in rows]
    print("\n%d trades, %d won (%.0f%%), net Rs %+.0f, average Rs %+.0f per trade." % (
        len(nets), sum(x > 0 for x in nets), 100 * sum(x > 0 for x in nets) / len(nets), sum(nets),
        sum(nets) / len(nets)))
    if len(nets) < 100:
        print("Fewer than 100 trades: far too few to judge any strategy. Keep going.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--symbols", help="comma list instead of the Nifty list, e.g. RELIANCE,TCS")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe")
    p = sub.add_parser("collect"); p.add_argument("--interval", default="5m", choices=["5m", "15m", "60m", "1d"])
    p = sub.add_parser("backtest"); p.add_argument("--interval", default="5m", choices=["5m", "15m", "60m"])
    p.add_argument("--strategy", choices=list(strategies.REGISTRY))
    p.add_argument("--portfolio", action="store_true", help="apply max trades/day + daily loss limit")
    p.add_argument("--trades", action="store_true", help="save every trade to results/ (needs --strategy)")
    sub.add_parser("daily")
    sub.add_parser("learn")
    p = sub.add_parser("replay"); p.add_argument("--date", required=True)
    p = sub.add_parser("paper"); p.add_argument("--once", action="store_true", help="one step, then exit")
    p.add_argument("--until", default="15:20", help="IST time to stop (e.g. 12:15 for a morning shift)")
    p = sub.add_parser("status"); p.add_argument("--running", action="store_true")
    p = sub.add_parser("report"); p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    cfg = risk.load_config(a.config)
    {"universe": cmd_universe, "collect": cmd_collect, "backtest": cmd_backtest, "daily": cmd_daily,
     "replay": cmd_replay, "learn": cmd_learn, "paper": cmd_paper, "report": cmd_report,
     "status": cmd_status}[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
