"""Pure parts of Ask retrieval: reciprocal rank fusion and time-window phrases (no DB)."""

from datetime import UTC, datetime

import pytest

from app.rag.filters import _window
from app.rag.retrieve import rrf

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def test_rrf_rewards_documents_in_both_lists():
    fused = rrf([["a", "b", "c"], ["c", "d"]], k=60)
    ids = [d for d, _, _ in fused]
    assert ids[0] == "c"  # 1/63 + 1/61 beats 1/61 alone
    assert ids[1:] == ["a", "b", "d"]  # a (1/61) > b (1/62) = d (1/62): tie keeps first-seen order
    c = fused[0]
    assert c[2] == [3, 1]
    assert c[1] == pytest.approx(1 / 63 + 1 / 61)
    assert dict((d, r) for d, _, r in fused)["d"] == [None, 2]


def test_rrf_counts_a_duplicate_once_and_handles_empty_lists():
    fused = rrf([["a", "a", "b"], []], k=60)
    assert [(d, r) for d, _, r in fused] == [("a", [1, None]), ("b", [3, None])]
    assert rrf([[], []], k=60) == []


@pytest.mark.parametrize(
    ("question", "since", "until", "label"),
    [
        ("What happened today?", datetime(2026, 10, 20, 8, tzinfo=UTC), None, "last 24 hours"),
        (
            "Supply-chain incidents this week",
            datetime(2026, 10, 14, 8, tzinfo=UTC),
            None,
            "last 7 days",
        ),
        (
            "What changed at Sonatype this month?",
            datetime(2026, 9, 21, 8, tzinfo=UTC),
            None,
            "last 30 days",
        ),
        (
            "launches in the last 90 days",
            datetime(2026, 7, 23, 8, tzinfo=UTC),
            None,
            "last 90 days",
        ),
        ("pricing changes this year", datetime(2026, 1, 1, tzinfo=UTC), None, "2026"),
        (
            "What did GitHub ship in October?",
            datetime(2026, 10, 1, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
            "October 2026",
        ),
        (
            "news in December",
            datetime(2025, 12, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            "December 2025",
        ),
        ("anything since July 2026?", datetime(2026, 7, 1, tzinfo=UTC), None, "since July 2026"),
        ("Who launched a package firewall?", None, None, None),
        ("What may Sonatype do next?", None, None, None),  # "may" is not a month here
    ],
)
def test_time_windows(question, since, until, label):
    assert _window(question, NOW) == (since, until, label)
