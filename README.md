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
python3 trade.py learn                         # nightly: learn from all history, walk-forward check
python3 trade.py learn --interval 60m          # the same learner on ~3 years of hourly bars (a check, not used to trade)
python3 trade.py ready                         # has any paper account earned a real-money trial?
python3 trade.py research                      # Round 2: seven new ideas on the design years (rules in nse/research.py)
python3 trade.py swing --collect               # Round 3: download daily prices for ~550 stocks (stays on this machine)
python3 trade.py swing                         # Round 3: six ideas that hold for days or weeks (rules in nse/swing.py)
python3 trade.py replay --date 2026-11-04      # live engine re-run on a past day, bar by bar (+ learner)
python3 trade.py paper                         # LIVE dummy trades, leave running 9:15-15:20 IST
python3 trade.py report                        # paper journal and running totals
python3 -m unittest discover -s tests -v       # offline tests
```

Every strategy in `config.json` -> `strategies` trades as its own paper
account (its own 3 trades a day and daily loss limit), so results can be
compared side by side. Settings live in `config.json`: capital (₹1,00,000 paper), risk per trade
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

## Learning from mistakes (every evening)

`python3 trade.py learn` (run automatically at 16:00 IST in the cloud, after the
day's bars are saved) re-tests every strategy on all saved 5-minute history and
tags each trade with five facts known at entry: long/short, time of day, with or
against the market's move, with or against the stock's opening gap, and stop
width. It learns which strategies and conditions have been losing (the
mistakes), writes `model/learned.json`, and the next day an 8th paper account,
**learner**, takes only the signals the model expects to make at least +0.05 R
after costs.

Guards against overfitting (all in `nse/learn.py`): few coarse facts fixed in
advance and judged one at a time; every estimate shrunk toward "no edge" (50
trades' worth of doubt); a condition needs 30+ trades before it counts; older
trades fade (60-day half-life); 20 days of warm-up; and a **walk-forward test**
where each past day is judged by a model fitted only on the days before it.
That fair-test result is on the phone page, next to the unfiltered baseline.

First run (4 Nov 2026, 60 days): the model would take only some `orb30_2r` and
`orb60` setups and nothing else. In the walk-forward test it lost Rs 112 a trade
(69 trades) against Rs 105 a trade for the unfiltered first 3 signals a day: it
**lost less in total by trading less, but did not find an edge**. Learning can
only filter what the strategies offer; it can't create an edge they don't have.

## Before any real money

**As of 10 Nov 2026 the answer is no.** Three paper days were up Rs 3,359 over
66 trades. That is one good day and two small losing days: far too little to
tell skill from luck. The long tests all say the same thing:

| test | trades | Rs per trade | t-stat |
| --- | --- | --- | --- |
| 5-minute history, 63 days: learner, fair test | 78 | −73 | −1.9 |
| 5-minute history, 63 days: first 3 signals a day | 129 | −98 | −4.4 |
| Hourly, 3 years (724 days): learner, fair test | 392 | −51 | −2.2 |
| Hourly, 3 years: first 3 signals a day | 2,112 | −42 | −4.9 |
| Hourly, 3 years: orb60 only "with market, wide stop" (the best lesson) | 4,300 | −30 | −6.8 |

The lessons are real but small: trading with the market and with a wide stop
cuts the loss per trade by more than half, mostly because charges are a smaller
share of a wide stop. Nothing reaches profit. Before charges these setups earn
about zero, and a filter cannot add what is not there. Tuning more settings
until some backtest turns green would be overfitting, so that was not done.

`nse/gate.py` holds the test an account must pass before real money is worth
even a small trial. The rules were fixed on 10 Nov 2026, with 3 days of
results, and must not be loosened later. All seven must hold for one account's
live paper trades:

1. 60+ trading days of paper trading
2. 150+ trades
3. profit per trade above zero after all charges
4. t-statistic of 3 or more (not 2: eight accounts run side by side, and the
   best of eight clears 2 by luck about one time in six)
5. profitable in the first half and in the second half
6. worst losing run under Rs 10,000
7. also profitable on saved history (backtest for a strategy, fair test for
   the learner)

`python3 trade.py ready` prints the verdict, and the phone page shows it at
the top. Passing means "worth a small trial", never "safe". Real trading is
harder than this paper account: orders queue and fill partly, the free data
here is delayed and sometimes wrong, and shorting needs margin and can be
blocked. Expect real results to be worse than paper results.

## Round 2: seven different kinds of strategy (10 Nov 2026)

The first seven strategies all trade breakouts, gaps and VWAP. Round 2 asked
whether a different *kind* of idea does better. "Better" means rupees per trade
after charges on days the idea was not built on, not win rate (see trap90
above).

The test rules were committed to git before any idea was run on any data
(`nse/research.py`), so the result could not be steered:

- settings are round numbers chosen in advance; nothing is tuned
- 3 years of hourly bars are cut in two: **design years** (478 days, before
  1 Nov 2025) and an **exam year** (246 days after) that is not looked at
- an idea reaches the exam only if it made money after charges in the design
  years, and passes only with profit and a t-statistic by day of 2.5 or more
- passing earns a paper account, which must still pass the real-money gate

Design years, every signal, 50 stocks:

| idea | what it does | trades | won | per trade before charges | after charges | t by day |
| --- | --- | --- | --- | --- | --- | --- |
| leaders | 11:15: buy stocks 1 %+ ahead of the market, short those 1 %+ behind | 5,682 | 42 % | +0.01 R | −Rs 46 | −6.5 |
| laggards | the mirror image: bet the gap to the market closes | 5,682 | 43 % | −0.08 R | −Rs 91 | −15.8 |
| trend_day | 11:15: market has moved 0.4 %+, join the strong stocks | 2,085 | 41 % | 0.00 R | −Rs 50 | −3.1 |
| late_push | 14:15: market and stock moved the same way all day, ride to the close | 3,817 | 40 % | +0.02 R | −Rs 59 | −4.5 |
| daily_trend | first-hour breakout in the direction of the 20-day trend | 5,829 | 41 % | −0.01 R | −Rs 43 | −9.2 |
| squeeze | first-hour breakout after the quietest day in 7 | 2,321 | 40 % | −0.01 R | −Rs 45 | −5.0 |
| bounce | 3 lower closes and down 4 %+: buy a green first hour (and the mirror) | 974 | 42 % | −0.02 R | −Rs 59 | −3.0 |

**No idea made money after charges, so none went to the exam.** The exam year
was left unopened and no paper account was added. Before charges the best idea
earns +0.02 R a trade (about Rs 10); charges then take Rs 40 to 70.

### Why: these stocks made their gains while the market was shut

`research` also splits each day's move of the average Nifty 50 stock in two
(723 days, before charges):

| | per day | 3-year total | t |
| --- | --- | --- | --- |
| while the market was shut (close to next open) | +0.084 % | +61 % | 4.3 |
| while the market was open (open to close) | −0.026 % | −19 % | −1.0 |

48 of the 50 stocks gained while the market was shut; only 18 gained while it
was open. By year (shut / open): 2024 +32 % / −13 %, 2025 +18 % / −4 %, 2026 so
far +3 % / −4 %.

An intraday rule must be flat by 15:15, so it never holds the part of the day
where the gains were made. What is left, open to close, averaged about zero,
and none of the 14 intraday rules tried here found a pattern in it that
survives charges. Trying more intraday rules until one backtest turns green
would be overfitting.

Cautions on the overnight number. It is not a strategy by itself: buying at
the close and selling at the next open every day pays delivery charges of
about 0.2 % a round trip (STT alone is 0.1 % a side), against 0.084 % earned,
and it faded to +3 % in 2026. The 50 stocks are today's Nifty 50, which
flatters the past: stocks that dropped out of the index are not in the data.
And three years is one stretch of one market.

## Round 3: holding for days or weeks (10 Nov 2026)

Round 2 found that these stocks made their gains while the market was shut,
which a same-day rule never holds. Round 3 asked whether rules that hold for
days or weeks do better. As before, the rules were committed to git before any
idea was run on real prices (`nse/swing.py`, commit 3adee21):

- buy only, shares paid for in full, decided and filled at the day's close;
  Rs 1 lakh in 5 equal positions; today's delivery charges, about 0.40 % for a
  round trip including slippage
- stocks picked without hindsight: each month the 50 most traded, drawn from
  today's Nifty 500 plus 47 once heavily traded names that have since left it
  (Unitech, RCom, HDIL, Jet Airways and others). 165 stocks pass through
- **design years** 2010 to 2022 (3,207 days) and **exam years** 2023 to
  9 Nov 2026 (949 days) that are not looked at while choosing
- to reach the exam an idea's average trade must make money after charges,
  beat holding the 50 stocks over the same days, and beat the Nifty 50 index
  over the same days. To pass the exam it must do all three again, with a
  t-statistic by month of 2.5 or more

Design years, every signal:

| idea | what it does | trades | days held | after charges | holding the 50 | excess | t | Rs 1 lakh became |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| momentum | month end: the 5 biggest winners of the past year | 272 | 59 | +2.25 % | +2.38 % | −0.13 % | 0.0 | 2.03 lakh |
| low_vol | month end: the 5 steadiest stocks | 295 | 54 | +4.21 % | +2.22 % | +1.99 % | 2.2 | 7.69 lakh |
| high52 | first new one-year high; out after 20 days or at −8 % | 876 | 18 | +0.13 % | +0.45 % | −0.31 % | −1.7 | 0.95 lakh |
| pullback | 5 % dip over 3 days in an uptrend; out on the bounce or after 10 days | 1,759 | 4 | +0.23 % | +0.24 % | 0.00 % | 0.2 | 0.55 lakh |
| losers | week end: the 5 biggest fallers, for a week | 2,915 | 5 | −0.20 % | +0.21 % | −0.41 % | −3.6 | 0.05 lakh |
| volume_surge | up 4 %+ on 3 times the usual volume; out after 10 days or at −8 % | 761 | 9 | −0.25 % | +0.20 % | −0.46 % | −1.2 | 0.62 lakh |
| *holding the 50 stocks, no charges* | | | | | | | | 2.58 lakh |
| *Nifty 50 index, no dividends* | | | | | | | | 3.46 lakh |

"Excess" is the trade after charges minus what the 50 stocks did over the same
days. Only `low_vol` qualified, so only `low_vol` was run on the exam years:

| low_vol | trades | after charges | holding the 50 | excess | t | Rs 1 lakh became | a year | worst fall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| design years 2010 to 2022 | 295 | +4.21 % | +2.22 % | +1.99 % | 2.2 | 7.69 lakh | 17.0 % | −36 % |
| **exam years 2023 to 2026** | 97 | +1.27 % | +2.99 % | **−1.72 %** | −1.7 | 1.22 lakh | 5.2 % | −31 % |
| *holding the 50 stocks, exam years* | | | | | | 1.59 lakh | 12.9 % | −24 % |
| *Nifty 50 index, exam years* | | | | | | 1.38 lakh | 8.6 % | −16 % |

**It failed, so no idea passed and no paper account was added.** In the exam
years `low_vol` made money, but less than doing nothing: the Nifty 50 index
made 8.6 % a year against its 5.2 %, with half the worst fall.

What the detail shows:

- `low_vol` looked like a rule and was largely one stock's decade. HDFC Bank,
  bought in April 2014 and held to September 2019, made +240 %. Without its six
  best trades the design-years excess is +0.75 %, not +1.99 %. That is what an
  exam on unseen years is for.
- `pullback`: the average dip-buy made +0.23 %, but all of it came from days
  with more than 5 signals at once (+1.21 % each). A 5-position account cannot
  take them all, and on the other days the average was −0.05 %. Taking trades
  as they came, the account lost 4.5 % a year.
- `losers` trades every week, so charges alone take about 0.4 % a week.
- In the design years the Nifty 50 index (10.0 % a year) beat an equal holding
  of the 50 most traded stocks (7.6 %): the most traded list is where the
  story stocks are. The index beat five of the six ideas in the design years
  and the sixth in the exam years.

Cautions. The free data has no prices for 98 of the 145 former big names
looked for (JP Associates, Reliance Capital, DHFL, Kingfisher, Satyam among
them), and leaving failures out flatters every idea here. Fills are assumed at
the closing price. Today's charges are applied to every year; a broker that
charges Rs 20 per delivery order adds another 0.2 % a round trip. Dividends are
not counted for the ideas, the 50 stocks or the index.

Across three rounds, 20 rule-based ideas have now been tested on prices they
were not built on. None beat charges and plain holding.

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

Judge a strategy only on ₹ per trade after costs, never on win rate, and only
with the gate in "Before any real money" above. If an account ever passes it,
the next step is a tiny real position with a broker's API (Zerodha Kite
Connect, Upstox, Dhan), with every order approved by hand. That is not built
here on purpose.

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
- `.github/workflows/collect.yml` saves the day's 5-minute bars at 16:00 IST,
  then runs `learn` and saves `model/learned.json` for the next morning.
- `docs/` is the phone page (GitHub Pages: Settings > Pages > Deploy from a
  branch > `main` / `docs`). It reads `docs/status.json`.
- GitHub's own schedule often starts hours late, so cron-job.org starts the
  paper workflow on time by calling GitHub's "run workflow" API at 09:10 and
  12:15 IST. The schedule in the file is only a backup.
- Stop: GitHub app > Actions > the running job > Cancel. Turn off completely:
  Actions > Paper trading > ... > Disable workflow (and pause cron-job.org).
