# How day-trader-skill works, and what changes for NSE

This maps the original repo (github.com/Jakeschincariol/day-trader-skill,
13,000 lines of standard-library Python, 315 offline tests) piece by piece.

## The pipeline

Each skill writes a JSON file under `trader/` that the next one reads:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker
             (alerts watch the open trade; analyst/pelosi run on a schedule)
```

| module | what it really does inside | US-only parts | NSE version (this folder) |
| --- | --- | --- | --- |
| `trader-scanner/scan.py` | Downloads the Nasdaq Trader symbol list (~5,900 stocks), pulls daily closes in batches of 20 through Yahoo's "spark" endpoint, flags five daily setups (20-day breakout, bull flag, reclaim of 20-day MA, RSI<30, oversold bounce). Its `orb` mode pulls 5-min bars and lists stocks whose first close after the opening range is above the range high. | Nasdaq symbol files, 9:30-16:00 ET, NYSE holidays hard-coded for 2026-27, DST maths | `nse/universe.py` reads NSE's own Nifty 50/100 CSV; `nse/strategies.py` holds the setups; sessions come from IST 9:15-15:30 |
| `trader-news/news.py` | Yahoo RSS headlines + SEC 8-K filings, tags them (earnings, deal, ...) | SEC EDGAR | Not ported. Indian equivalent would be NSE/BSE corporate announcements. |
| `trader-entry/entry.py` | Watches one stock bar by bar; says waiting / triggered / expired. Never uses a bar that has not closed. | ET clock | `nse/paper.py` does the same for every stock in the watchlist, with the same strategy code the backtest uses |
| `trader-exit/exit.py` | Stop rules: opening-range mid, OR low, %, ATR multiple, fixed price. Target = N x risk ("R"). Refuses stops tighter than 0.05 % of price. | dollars/cents | Built into each strategy in `nse/strategies.py`, rupee tick 0.05 |
| `trader-timemachine/timemachine.py` | Monte-Carlo: rebuilds the rest of the day 10,000 times from same-time-of-day 5-min bars of the last 60 days, stop checked before target on every bar. Plus the "history": the same rule on each past session. | 78 bars/day | `nse/backtest.py` is the "history" part done properly: every stock, every session, after Indian costs. (The Monte-Carlo replay was left out: it resamples bars independently and so cannot see the patterns a strategy trades on.) |
| `trader-budget/size.py` | Shares = floor(max loss per trade / risk per share), capped by budget and max position %. | USD defaults ($50 max loss) | `nse/risk.py`, rupee defaults in `config.json` |
| `trader-risk/check.py` | PASS/BLOCK per order: time window, daily loss limit, max trades/day, reward:risk, stop required, no shorts/margin/options, PDT counter. | FINRA PDT, Alpaca order fields | `nse/risk.py`: time window, square-off time, trades/day, daily loss stop. Intraday shorting is allowed in India's cash segment (MIS), so shorts are on. |
| `trader-broker/ticket.py` | Turns an order into an Alpaca bracket order; only sends after the user types "yes". Paper by default. | Alpaca | `nse/paper.py` is paper only. A real broker (Zerodha Kite / Upstox / Dhan) is deliberately not wired in. |
| `trader-alerts/alerts.py` | Price alerts from the plan (stop, target, flat-by). | | Part of the paper engine's position management |
| `trader-analyst`, `trader-pelosi` | SEC XBRL earnings facts; US Congress trade disclosures. | US-only by nature | Not ported |

## Rules worth keeping (and kept)

1. **No look-ahead.** Only closed bars are used. In this NSE version the
   backtest and the live paper engine call the *same* `scan()` function on
   the bars seen so far, so a backtest cannot use information the live
   engine would not have had.
2. **Stop before target.** If one bar touches both, it counts as the stop.
3. **Paper first, human approval for anything real.**
4. **Say plainly when it is a coin flip.**

## What differs in India

- Session 9:15-15:30 IST, no daylight saving: 75 five-minute bars a day.
  Brokers auto-square-off intraday (MIS) positions around 15:15-15:25, so the
  system flattens at 15:15.
- Costs are much heavier than at a US zero-commission broker. Per round trip
  on ₹1 lakh of turnover each side: STT 0.025 % on the sell side, exchange
  charge ~0.003 %, SEBI fee, stamp duty 0.003 % on the buy side, GST 18 % on
  brokerage + exchange + SEBI fees, brokerage (₹20 or 0.03 % per order at
  discount brokers). See `nse/costs.py`.
- Holidays: taken from the data itself (a day with no bars is not a session)
  instead of a hard-coded list that goes stale.
- Free data limits (Yahoo, `.NS` suffix): 5-minute bars for the last 60
  days, 60-minute bars for ~2 years, daily bars back to the 1990s-2000s.
  `collect` saves 5-minute bars every day so the intraday history grows.
