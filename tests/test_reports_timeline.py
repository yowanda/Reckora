"""Tests for the timeline reconstruction helper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from reckora.evidence.chain import make_evidence
from reckora.models.entity import Identifier, Trace
from reckora.models.enums import IdentifierType, TraceSource
from reckora.reports.timeline import TimelineEntry, build_timeline


def _trace(value: str, *, fetched_at: datetime) -> Trace:
    ident = Identifier(type=IdentifierType.USERNAME, value=value)
    evidence = make_evidence(
        f"https://example.com/{value}",
        {"login": value},
        fetched_at=fetched_at,
    )
    return Trace(
        identifier=ident,
        source=TraceSource.GITHUB_API,
        fields={"platform": "github"},
        evidence=evidence,
    )


def test_build_timeline_empty() -> None:
    assert build_timeline([]) == []


def test_build_timeline_sorts_ascending() -> None:
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    later = _trace("late", fetched_at=base + timedelta(hours=2))
    earlier = _trace("early", fetched_at=base)
    middle = _trace("mid", fetched_at=base + timedelta(hours=1))

    timeline = build_timeline([later, earlier, middle])

    assert [e.identifier_value for e in timeline] == ["early", "mid", "late"]


def test_build_timeline_breaks_ties_with_payload_sha256() -> None:
    """Equal timestamps fall back to ``payload_sha256`` for stable ordering."""
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    a = _trace("a", fetched_at=fixed)
    b = _trace("b", fetched_at=fixed)

    forward = build_timeline([a, b])
    reversed_ = build_timeline([b, a])

    assert forward == reversed_
    sorted_ids = sorted([a.evidence.payload_sha256, b.evidence.payload_sha256])
    assert [e.evidence_sha256 for e in forward] == sorted_ids


def test_timeline_entry_short_sha_matches_dossier_shorthand() -> None:
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trace = _trace("alice", fetched_at=fixed)
    [entry] = build_timeline([trace])

    assert isinstance(entry, TimelineEntry)
    assert entry.evidence_sha256_short == trace.evidence.payload_sha256[:16]
    assert len(entry.evidence_sha256_short) == 16


def test_timeline_entry_carries_archive_and_screenshot_when_present() -> None:
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    trace = _trace("alice", fetched_at=fixed)
    augmented = trace.model_copy(
        update={
            "evidence": trace.evidence.model_copy(
                update={
                    "archive_url": "https://web.archive.org/x",
                    "screenshot_path": "/screenshots/alice.png",
                }
            )
        }
    )

    [entry] = build_timeline([augmented])

    assert entry.archive_url == "https://web.archive.org/x"
    assert entry.screenshot_path == "/screenshots/alice.png"


def test_timeline_entry_is_frozen() -> None:
    fixed = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    [entry] = build_timeline([_trace("alice", fetched_at=fixed)])
    try:
        entry.identifier_value = "mallory"
    except Exception:
        return
    raise AssertionError("TimelineEntry must be frozen")
