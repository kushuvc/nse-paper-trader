# NSE Day Trader: research and paper trading

An Indian-market rebuild of [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(see `REPO_MAP.md` for how the original works and what changed). It uses only
Python's standard library (3.8+), so there is nothing to install. **Paper
trading only: it cannot place a real order.**

## Commands

```bash
python3 trade.py universe                      # Nifty 50 from NSE's own list
python3 trade.py collect --interval 5m         # run EVERY EVENING after 15:35: grows the 5-min history
python3 trade.py collect --interval 60m        # ~2 years hourly
python3 trade.py collect --interval 1d         # full daily history (1990s on)
python3 trade.py backtest --interval 5m        # all strategies, 50 stocks, after Indian costs
python3 trade.py backtest --interval 5m --portfolio          # with 3 trades/day + daily loss limit
python3 trade.py backtest --interval 5m --strategy orb15 --trades   # every trade -> results/*.csv
python3 trade.py backtest --interval 60m
python3 trade.py daily                         # 20+ year open-to-close gap study (read its warning)
python3 trade.py replay --date 2026-11-04      # live engine re-run on a past day, bar by bar
python3 trade.py paper                         # LIVE dummy trades, leave running 9:15-15:20 IST
python3 trade.py report                        # paper journal and running totals
python3 -m unittest discover -s tests -v       # offline tests
```

Settings live in `config.json`: capital (₹1,00,000 paper), risk per trade
(₹500), max 3 trades a day, ₹1,500 daily loss stop, no entries before 9:30 or
after 14:30, square-off at 15:15, strategies to run. Edit `nse/costs.py` or the
`"costs"` key if your broker charges differently.

## What the data says (run on 4 Nov 2026)

Nifty 50 stocks, ₹500 risk per trade, all charges (brokerage, STT, exchange,
SEBI, stamp duty, GST) plus 0.02 % slippage each side. "Need %" is the win
rate required to break even with that strategy's own average win and loss.

**5-minute bars, 60 sessions (Aug 10 to Nov 4 2026), every signal:**

| strategy | trades | win % before costs | win % after | need % | ₹ per trade | t-stat |
| --- | --- | --- | --- | --- | --- | --- |
| orb15 (repo's original setup) | 2,785 | 50.5 | 48.8 | 61.9 | −101 | −14.4 |
| orb30_2r | 2,649 | 48.8 | 43.6 | 51.4 | −51 | −6.6 |
| orb60 | 2,436 | 51.0 | 47.3 | 59.2 | −85 | −11.5 |
| gap_go | 166 | 38.0 | 36.7 | 53.9 | −148 | −4.4 |
| gap_fade | 64 | 37.5 | 35.9 | 36.1 | −2 | −0.8 |
| vwap | 1,928 | 39.0 | 37.0 | 49.7 | −102 | −12.5 |
| **trap90** (tiny target, wide stop) | 1,246 | **83.9** | 79.7 | **99.5** | −59 | −13.7 |

**60-minute bars, ~720 sessions (Nov 2023 to Nov 2026):** orb60 −₹73/trade
over 23,296 trades. gap_go60 −₹22 and gap_fade60 −₹38: both slightly positive
*before* costs (+0.04R), negative after.

**Daily bars, 1991-2026:** "gap fade" looks spectacular (+0.76 % a trade,
34 of 36 years positive). **It is fake.** Yahoo's daily open is off by more
than 0.5 % from the real first traded price on ~7 % of days. The "edge" shrinks
from ~1 % before 2005 to 0.06 % after 2024, and it **loses** money when tested
with real 5-minute opens. This is exactly how a backtest fools people into
believing in 90 % systems.

### What that means

- **None of these setups makes money after Indian costs.** Charges alone take
  ₹40-80 a trade. On ₹500 risk that is 0.1-0.15R per trade before you start.
- **trap90 wins 84 % of trades and still loses money.** One ₹300 stop wipes
  out about 150 of its ₹2 wins. A 90 % win rate is easy to build. A *profitable*
  one is the hard part, and the win rate doesn't tell you which you have.
- 60 days of 5-minute data is short. Keep running `collect` every evening:
  after 6-12 months the 5-minute tests mean much more.

## Paper trading ("dummy live trades")

1. In the evening: `python3 trade.py collect --interval 5m`
2. Before 9:15 IST: `python3 trade.py paper` in a terminal and leave it open.
   It wakes 20 s after every 5-minute bar, logs OPEN / SKIP / CLOSE, and
   squares everything off at 15:15.
3. After the close: `python3 trade.py report`.

The live engine calls the same strategy and exit code as the backtest. The
replay of 4 Nov 2026 produced exactly the backtest's trades (same stocks,
quantities, entries, exits and ₹), and a test checks this on every run.

Or let the Mac do steps 1-2 every weekday: `./schedule.sh on` (`off` to
remove, `status` to check). Paper trading starts at 9:10 IST, data is saved at
15:45 IST, logs go to `logs/`, and a notification shows the day's result. The
Mac must be awake with the lid open at 9:10.

Judge a strategy only after **100+ paper trades** and only on ₹ per trade
after costs, never on win rate. If paper results ever stay positive over
several months, the next step is a tiny real position with a broker's API
(Zerodha Kite Connect, Upstox, Dhan), with every order approved by hand.
That is not built here on purpose.

## Limits

- Yahoo data is free and unofficial. It can lag, have gaps, or be wrong (see
  above). Real trading needs broker or exchange data.
- Bars, not ticks: fills assume the bar's close plus slippage. In fast markets
  real fills can be worse.
- No news, results-day or F&O-ban filters yet.
- Nifty 50 membership is today's list, applied to the past (survivorship bias,
  which flatters results if anything).

**Not financial advice.** SEBI's own studies found that roughly 7 in 10
individual intraday traders in the cash segment lost money (FY23) and roughly
9 in 10 individual F&O traders lost money.

## From your phone

- **Dashboard (same Wi-Fi):** `python3 dashboard.py` on the Mac prints a link
  with a private access key. Open it on the phone: Start / Stop paper
  trading, Save data, open positions, today's trades, all-time totals, and the
  live log, refreshing every 15 s. Keep the Mac awake and the dashboard window
  open. If macOS asks whether Python may accept incoming connections, allow it.
- **Away from home:** use the Claude mobile app with this session's Remote
  Control turned on, and ask Claude to start, stop or report.

## Running in the cloud (GitHub, free for a public repo)

- `.github/workflows/paper.yml` paper-trades on weekdays in two shifts
  (09:10-12:15 and 12:15-15:20 IST: a GitHub job can run at most 6 hours) and
  saves `paper/`, `logs/` and `docs/status.json` back to the repo every 10 min.
- `.github/workflows/collect.yml` saves the day's 5-minute bars at 16:00 IST.
- `docs/` is the phone page (GitHub Pages: Settings > Pages > Deploy from a
  branch > `main` / `docs`). It reads `docs/status.json`.
- GitHub's own schedule often starts hours late, so cron-job.org starts the
  paper workflow on time by calling GitHub's "run workflow" API at 09:10 and
  12:15 IST. The schedule in the file is only a backup.
- Stop: GitHub app > Actions > the running job > Cancel. Turn off completely:
  Actions > Paper trading > ... > Disable workflow (and pause cron-job.org).
