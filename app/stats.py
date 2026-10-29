"""Statistics per exam (CLAUDE.md §6): hit rate per snapshot topic and results over time."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models import Attempt

# Line chart geometry (SVG user units); the template only loops over what is computed here.
WIDTH, HEIGHT, PAD = 600, 160, 12
TICKS = (0, 50, 100)


@dataclass(frozen=True)
class TopicStat:
    topic: str
    answered: int
    correct: int

    @property
    def percent(self) -> float:
        return 100 * self.correct / self.answered


def topic_stats(counts: Iterable[tuple[str, int, int]]) -> list[TopicStat]:
    """(topic, answered, correct) rows, weakest topic first."""
    stats = [TopicStat(*row) for row in counts]
    return sorted(stats, key=lambda s: (s.percent, s.topic.casefold()))


def y_of(percent: float) -> float:
    return PAD + (HEIGHT - 2 * PAD) * (1 - percent / 100)


@dataclass(frozen=True)
class ChartPoint:
    attempt: Attempt
    x: float
    y: float


@dataclass(frozen=True)
class LineChart:
    points: list[ChartPoint]
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def ticks(self) -> list[tuple[int, float]]:
        return [(tick, y_of(tick)) for tick in TICKS]

    @property
    def inset(self) -> float:
        """Horizontal padding as a percentage of the width, for the HTML axis labels."""
        return 100 * PAD / WIDTH

    @property
    def polyline(self) -> str:
        return " ".join(f"{p.x:.1f},{p.y:.1f}" for p in self.points)


def history_chart(history: Sequence[Attempt]) -> LineChart | None:
    """Score percentages of finished attempts, oldest first (history comes newest first);
    attempts without points are left out. None below two points: no line to draw."""
    scored = [a for a in reversed(history) if a.max_score]
    if len(scored) < 2:
        return None
    step = (WIDTH - 2 * PAD) / (len(scored) - 1)
    return LineChart([ChartPoint(a, PAD + i * step, y_of(a.percent)) for i, a in enumerate(scored)])
