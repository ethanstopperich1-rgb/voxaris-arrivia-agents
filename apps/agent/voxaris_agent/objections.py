"""Top 100 Objections lookup.

Source: TOP 100 OBJECTIONS.docx — 100 objections across 6 categories
(time, sales resistance, spouse/decision-makers, travel, financial,
general brush-offs). Each entry has a single recommended rebuttal that
ends with a soft trial close.

Matching strategy: token-overlap (Jaccard) over normalized words. With
100 short entries, this is fast, deterministic, and good enough — no
embedding model required. If callers say something genuinely novel,
the matcher returns None and the agent improvises (which is fine: the
persona already constrains tone).

Surface: a single tool function `lookup_objection(text, incentive)`
returning a small dict the model can read.
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from importlib.resources import files
from typing import Sequence

# Tokens we drop before comparing — they're noise in short objections.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "do", "for",
        "from", "have", "i", "in", "is", "it", "its", "me", "my", "of", "on",
        "or", "our", "so", "that", "the", "this", "to", "us", "we", "with",
        "you", "your", "yours", "im", "ive", "id", "ill", "dont", "wont",
        "cant", "wouldnt", "couldnt", "shouldnt", "havent", "hasnt",
        "arent", "isnt", "werent", "hadnt", "didnt", "doesnt",
    }
)


@dataclass(frozen=True)
class Match:
    category: str
    objection: str
    rebuttal: str
    score: float


def _normalize(text: str) -> str:
    """Lowercase, strip curly quotes & punctuation, collapse whitespace."""
    t = text.lower()
    # Curly quotes / apostrophes — common in the source doc.
    t = t.translate(str.maketrans("’‘“”—–", "''\"\"--"))
    t = t.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", t).strip()


def _tokenize(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if w and w not in _STOPWORDS}


def _load() -> list[dict]:
    raw = files("voxaris_agent.data").joinpath("objections.json").read_text(
        encoding="utf-8"
    )
    return json.loads(raw)


_DATA: list[dict] = _load()
_INDEX: list[tuple[set[str], dict]] = [(_tokenize(o["objection"]), o) for o in _DATA]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_objection(
    text: str,
    *,
    threshold: float = 0.2,
    top_k: int = 1,
) -> list[Match]:
    """Return up to `top_k` best objection matches above `threshold`.

    Empty list means "no confident match — let the model improvise."
    """
    if not text or not text.strip():
        return []
    q = _tokenize(text)
    scored: list[Match] = []
    for tokens, entry in _INDEX:
        s = _jaccard(q, tokens)
        if s >= threshold:
            scored.append(
                Match(
                    category=entry["category"],
                    objection=entry["objection"],
                    rebuttal=entry["rebuttal"],
                    score=s,
                )
            )
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_k]


def render_rebuttal(rebuttal: str, incentive: str | None = None) -> str:
    """Substitute `[incentive]` placeholder so the agent reads natural copy.

    The source doc uses literal `[incentive]` in one rebuttal as a slot
    for whatever's currently being offered.
    """
    if not incentive:
        return rebuttal
    return rebuttal.replace("[incentive]", incentive)


__all__ = ["Match", "match_objection", "render_rebuttal"]


def categories() -> Sequence[str]:
    """Distinct categories, useful for diagnostics."""
    seen: list[str] = []
    for o in _DATA:
        c = o["category"]
        if c not in seen:
            seen.append(c)
    return seen


def count() -> int:
    return len(_DATA)
