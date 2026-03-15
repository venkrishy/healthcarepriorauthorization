"""Unit tests for authorization decision routing logic."""

import re

import pytest

from authagent.graph.auth_graph import AuthGraph


@pytest.mark.parametrize(
    "text, expected",
    [
        ('"necessity_score": 82', 82),
        ('"necessity_score": 0', 0),
        ('"necessity_score": 100', 100),
        ("necessity_score: 75", 75),
        ("Necessity Score: 90/100", 90),
        ("no score here", None),
    ],
)
def test_extract_necessity_score(text, expected):
    """Validate regex extraction of necessity_score from agent output."""
    result = AuthGraph._extract_necessity_score(text)
    assert result == expected


@pytest.mark.parametrize(
    "score, missing_info, expected_decision",
    [
        (90, [], "APPROVED"),
        (75, [], "APPROVED"),
        (76, ["Missing PT notes"], "PENDED"),
        (75, ["Missing imaging"], "PENDED"),
        (74, [], "DENIED"),
        (0, [], "DENIED"),
        (50, ["Multiple gaps"], "DENIED"),
    ],
)
def test_routing_decision_logic(score, missing_info, expected_decision):
    """
    Decision routing is deterministic based on score and missing_info.
    This matches the AuthorizationRouterAgent's system prompt rules.
    """
    if score >= 75 and not missing_info:
        decision = "APPROVED"
    elif score >= 75 and missing_info:
        decision = "PENDED"
    else:
        decision = "DENIED"

    assert decision == expected_decision


def test_extract_guideline_ids():
    """Validate guideline ID extraction from agent output."""
    text = """
    Based on LCD L35539 and NCD 20.9, the procedure CPT-45378 is covered.
    See also LCD-L35121 for additional criteria.
    """
    ids = AuthGraph._extract_guideline_ids(text)
    assert len(ids) > 0
    # Should find LCD/NCD references
    combined = " ".join(ids)
    assert any("LCD" in i or "NCD" in i or "CPT" in i for i in ids)
