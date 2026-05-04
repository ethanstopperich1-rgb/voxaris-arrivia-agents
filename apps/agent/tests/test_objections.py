"""Tests for the Top 100 Objections lookup."""

from __future__ import annotations

import pytest

from voxaris_agent.objections import (
    Match,
    categories,
    count,
    match_objection,
    render_rebuttal,
)


def test_dataset_loads_150_entries() -> None:
    assert count() == 150


def test_six_categories_present() -> None:
    cats = set(categories())
    assert {
        "TIME / COMMITMENT",
        "SALES RESISTANCE",
        "SPOUSE / DECISION MAKERS",
        "TRAVEL / SITUATIONAL",
        "FINANCIAL / QUALIFICATION",
        "GENERAL RESISTANCE / BRUSH-OFFS",
    }.issubset(cats)


def test_verbatim_match_returns_top_score() -> None:
    res = match_objection("We don't have time.")
    assert res, "expected at least one match"
    assert res[0].category == "TIME / COMMITMENT"
    assert res[0].score >= 0.5


@pytest.mark.parametrize(
    "phrase, expected_category",
    [
        ("they're too busy this week", "TIME / COMMITMENT"),
        ("we already have plans", "TIME / COMMITMENT"),
        ("don't want to be pressured into anything", "SALES RESISTANCE"),
        ("we hate sales pitches", "SALES RESISTANCE"),
        ("my spouse isn't here", "SPOUSE / DECISION MAKERS"),
        ("we don't make enough", "FINANCIAL / QUALIFICATION"),
        ("we are saving money right now", "FINANCIAL / QUALIFICATION"),
    ],
)
def test_fuzzy_phrases_route_to_right_category(
    phrase: str, expected_category: str
) -> None:
    res = match_objection(phrase, top_k=3)
    assert res, f"no match for {phrase!r}"
    cats = [m.category for m in res]
    assert expected_category in cats, (
        f"{phrase!r} expected category {expected_category}, got {cats}"
    )


def test_unrelated_phrase_returns_no_match() -> None:
    res = match_objection("the weather in Reykjavik this Tuesday")
    assert res == []


def test_empty_input_returns_no_match() -> None:
    assert match_objection("") == []
    assert match_objection("   ") == []


def test_incentive_substitution() -> None:
    """The "We don't have time" rebuttal contains [incentive] which must
    be substituted before the agent reads it."""
    res = match_objection("we don't have time")
    rebuttal = res[0].rebuttal
    assert "[incentive]" in rebuttal, (
        "source rebuttal should still contain the placeholder"
    )
    rendered = render_rebuttal(rebuttal, "complimentary three-night Orlando getaway")
    assert "[incentive]" not in rendered
    assert "complimentary three-night Orlando getaway" in rendered


def test_render_rebuttal_passthrough_when_no_incentive() -> None:
    assert render_rebuttal("Hello", None) == "Hello"
    assert render_rebuttal("[incentive]", None) == "[incentive]"


def test_match_dataclass_is_hashable_frozen() -> None:
    m = Match(category="X", objection="Y", rebuttal="Z", score=0.5)
    with pytest.raises((AttributeError, Exception)):
        m.score = 1.0  # type: ignore[misc]
