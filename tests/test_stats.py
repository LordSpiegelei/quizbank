from datetime import UTC, datetime, timedelta

from app.models import Attempt
from app.stats import HEIGHT, PAD, WIDTH, history_chart, topic_stats

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_topic_stats_weakest_first() -> None:
    rows = [("Algebra", 2, 1), ("geometrie", 2, 2), ("Zahlen", 1, 0), ("Analysis", 2, 1)]
    assert [(s.topic, s.percent) for s in topic_stats(rows)] == [
        ("Zahlen", 0),
        ("Algebra", 50),
        ("Analysis", 50),
        ("geometrie", 100),
    ]


def attempt(day: int, score: float, max_score: float) -> Attempt:
    return Attempt(exam_id=1, score=score, max_score=max_score, finished_at=T0 + timedelta(day))


def test_history_chart() -> None:
    newest_first = [attempt(3, 0, 0), attempt(2, 10, 10), attempt(1, 5, 10), attempt(0, 0, 10)]
    chart = history_chart(newest_first)
    assert [p.attempt.percent for p in chart.points] == [0, 50, 100]  # oldest first, no 0-point
    first, middle, last = chart.points
    assert (first.x, last.x) == (PAD, WIDTH - PAD)
    assert (first.y, middle.y, last.y) == (HEIGHT - PAD, HEIGHT / 2, PAD)
    assert history_chart(newest_first[:2]) is None  # one scored attempt: no line
