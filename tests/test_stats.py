from datetime import date, datetime, timedelta

from talki import stats


def row(day: date, words: int, mode="dictation", speech=10.0, duration=12.0, category="other", app="kate"):
    ts = datetime(day.year, day.month, day.day, 12).timestamp()
    return {"created_at": ts, "words": words, "duration": duration, "speech_seconds": speech,
            "category": category, "app": app, "mode": mode}


def test_compute_streaks_and_wpm():
    today = date(2026, 9, 26)
    rows = [row(today - timedelta(days=d), 20) for d in (0, 1, 2, 5, 6, 7, 8)]
    rows.append(row(today, 5, mode="transform"))
    s = stats.compute(rows, today)
    assert s.current_streak == 3
    assert s.longest_streak == 4
    assert s.total_words == 140
    assert s.edits == 1
    assert round(s.wpm) == 120


def test_single_day_is_not_a_streak():
    today = date(2026, 9, 26)
    s = stats.compute([row(today, 5)], today)
    assert s.current_streak == 0


def test_band():
    assert [stats.band(w) for w in (0, 1, 249, 250, 500, 750, 5000)] == [0, 1, 1, 2, 3, 4, 4]
