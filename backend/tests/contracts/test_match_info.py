"""MatchInfo invariant: status='substitute' ⇔ is_substitute=True.

The UI badges substitutions by reading `is_substitute`; the status
drives everything else. If the two disagree, the badge contradicts the
status — exactly the shape bug this validator prevents.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts import MatchInfo, MatchStatus


def test_matched_with_flag_false_accepted() -> None:
    MatchInfo(
        status=MatchStatus.matched,
        confidence=None,
        confidence_reasons=[],
        assumptions=[],
        is_substitute=False,
        substitute_reason=None,
        verify_notes=[],
    )


def test_substitute_with_flag_true_accepted() -> None:
    MatchInfo(
        status=MatchStatus.substitute,
        confidence=None,
        confidence_reasons=[],
        assumptions=[],
        is_substitute=True,
        substitute_reason="Closest in-stock.",
        verify_notes=[],
    )


def test_substitute_with_flag_false_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="status='substitute' requires is_substitute=True",
    ):
        MatchInfo(
            status=MatchStatus.substitute,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason="Closest in-stock.",
            verify_notes=[],
        )


def test_flag_true_without_substitute_status_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match=r"is_substitute=True requires status='substitute'",
    ):
        MatchInfo(
            status=MatchStatus.matched,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=True,
            substitute_reason="Closest in-stock.",
            verify_notes=[],
        )
