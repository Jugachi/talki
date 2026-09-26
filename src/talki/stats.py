from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

DICTATION_MODES = ("dictation", "scratchpad", "recovered")


@dataclass
class Stats:
    wpm: float = 0.0
    total_words: int = 0
    dictations: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    daily_words: dict[date, int] = field(default_factory=dict)
    by_category: Counter = field(default_factory=Counter)
    by_app: Counter = field(default_factory=Counter)
    edits: int = 0


def _streaks(days: set[date], today: date) -> tuple[int, int]:
    longest = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    cur = 0
    d = today if today in days else today - timedelta(days=1)
    while d in days:
        cur += 1
        d -= timedelta(days=1)
    # A streak only counts once it spans more than one day.
    return (cur if cur > 1 else 0), (longest if longest > 1 else 0)


def compute(rows, today: date | None = None) -> Stats:
    today = today or date.today()
    st = Stats()
    daily: dict[date, int] = defaultdict(int)
    recent = []
    for r in rows:
        if r["mode"] not in DICTATION_MODES:
            st.edits += 1
            continue
        d = datetime.fromtimestamp(r["created_at"]).date()
        daily[d] += r["words"]
        st.total_words += r["words"]
        st.dictations += 1
        st.by_category[r["category"] or "other"] += r["words"]
        if r["app"]:
            st.by_app[r["app"]] += r["words"]
        if 0 < r["duration"] < 400 and r["speech_seconds"] > 0:
            recent.append(r)
    recent = recent[-100:]
    secs = sum(r["speech_seconds"] for r in recent)
    if secs > 0:
        st.wpm = sum(r["words"] for r in recent) / secs * 60
    st.daily_words = dict(daily)
    st.current_streak, st.longest_streak = _streaks(set(daily), today)
    return st


def band(words: int) -> int:
    """Heatmap intensity 0-4, matching 0 / 1-249 / 250-499 / 500-749 / 750+."""
    if words <= 0:
        return 0
    return min(4, 1 + words // 250)
