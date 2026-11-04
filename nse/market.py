"""NSE clock, bars and sessions. IST is UTC+5:30 all year (no daylight saving)."""
import datetime
from collections import namedtuple

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
TICK = 0.05  # NSE equity tick size for most stocks

# t: IST datetime of the bar's START; v: volume
Bar = namedtuple("Bar", "t o h l c v")


def now_ist():
    return datetime.datetime.now(IST)


def ist(ts):
    return datetime.datetime.fromtimestamp(int(ts), IST)


def minutes(t):
    """Minutes since midnight for a datetime/time."""
    return t.hour * 60 + t.minute


def bars_per_session(interval_min):
    return (minutes(CLOSE) - minutes(OPEN)) // interval_min  # 75 for 5m


def round_tick(x, up=None):
    """Round to the 0.05 tick. up=True ceil, up=False floor, None nearest."""
    n = x / TICK
    if up is True:
        n = -(-n // 1)
    elif up is False:
        n = n // 1
    else:
        n = round(n)
    return round(n * TICK, 2)


def split_sessions(bars):
    """List of bars (any interval) -> ordered list of (date, [bars]) during 9:15-15:30."""
    days = {}
    for b in bars:
        m = minutes(b.t)
        if minutes(OPEN) <= m < minutes(CLOSE):
            days.setdefault(b.t.date(), []).append(b)
    return [(d, days[d]) for d in sorted(days)]


def full_sessions(bars, interval_min, min_fraction=0.95):
    """Only sessions with (almost) every bar: drops Muhurat/special sessions and data gaps."""
    need = int(bars_per_session(interval_min) * min_fraction)
    return [(d, s) for d, s in split_sessions(bars) if len(s) >= need]
