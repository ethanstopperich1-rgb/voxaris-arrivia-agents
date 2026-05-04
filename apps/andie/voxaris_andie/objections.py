"""GVR member-re-engagement objection lookup for Andie.

Source: 84-entry library generated from Grok multi-source research
(LoyaltyPoint, Acoustic, Personify, FTC enforcement patterns,
USAA/Navy Federal call-audit transcripts, Snipp/HBR retention research).

10 categories — covers way more ground than the 5 rebuttals in the
original GVR Call Transfer Script:
  - skepticism_trust (9)
  - time_pressure (8)
  - travel_fit (9)
  - cost_value (10)
  - privacy_data (8)
  - negative_past (9)
  - decision_maker (7)
  - channel_pref (8)
  - life_stage (7)
  - rejection (9)
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from importlib.resources import files
from typing import Sequence


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
class ObjectionMatch:
    category: str
    objection: str
    rebuttal: str
    score: float


def _normalize(text: str) -> str:
    t = text.lower()
    t = t.translate(str.maketrans("’‘“”—–", "''\"\"--"))
    t = t.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", t).strip()


def _tokenize(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if w and w not in _STOPWORDS}


def _load() -> list[dict]:
    raw = files("voxaris_andie.data").joinpath("objections.json").read_text(
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
) -> list[ObjectionMatch]:
    """Return up to `top_k` best objection matches above `threshold`.
    Empty list = no confident match — let the model improvise."""
    if not text or not text.strip():
        return []
    q = _tokenize(text)
    scored: list[ObjectionMatch] = []
    for tokens, entry in _INDEX:
        s = _jaccard(q, tokens)
        if s >= threshold:
            scored.append(
                ObjectionMatch(
                    category=entry["category"],
                    objection=entry["objection"],
                    rebuttal=entry["rebuttal"],
                    score=s,
                )
            )
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_k]


def count() -> int:
    return len(_DATA)


def categories() -> Sequence[str]:
    seen: list[str] = []
    for o in _DATA:
        if o["category"] not in seen:
            seen.append(o["category"])
    return seen


__all__ = ["ObjectionMatch", "match_objection", "count", "categories"]
