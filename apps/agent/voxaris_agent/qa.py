"""Arrivia Guest Q&A lookup.

Source: Arrivia_VBA_Guest_QA_v2.docx — 18 canonical answers across
4 sections (the free premium, the presentation, the deposit & booking,
qualifying for the presentation).

Use the same matcher pattern as objections.py — token-overlap Jaccard.
The agent calls `lookup_qa(question_text)` whenever the caller asks a
factual question (especially about the premium, presentation length,
deposit, eligibility, opt-out).
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
        "arent", "isnt", "werent", "hadnt", "didnt", "doesnt", "what",
        "how", "why", "when", "where", "who",
    }
)


@dataclass(frozen=True)
class QAMatch:
    section: str
    question: str
    answer: str
    score: float


def _normalize(text: str) -> str:
    t = text.lower()
    t = t.translate(str.maketrans("’‘“”—–", "''\"\"--"))
    t = t.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", t).strip()


def _tokenize(text: str) -> set[str]:
    return {w for w in _normalize(text).split() if w and w not in _STOPWORDS}


def _load() -> list[dict]:
    raw = files("voxaris_agent.data").joinpath("qa.json").read_text(
        encoding="utf-8"
    )
    return json.loads(raw)


_DATA: list[dict] = _load()
_INDEX: list[tuple[set[str], dict]] = [(_tokenize(e["question"]), e) for e in _DATA]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_qa(text: str, *, threshold: float = 0.15, top_k: int = 1) -> list[QAMatch]:
    if not text or not text.strip():
        return []
    q = _tokenize(text)
    scored: list[QAMatch] = []
    for tokens, entry in _INDEX:
        s = _jaccard(q, tokens)
        if s >= threshold:
            scored.append(QAMatch(
                section=entry["section"],
                question=entry["question"],
                answer=entry["answer"],
                score=s,
            ))
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_k]


def count() -> int:
    return len(_DATA)


def sections() -> Sequence[str]:
    seen: list[str] = []
    for e in _DATA:
        if e["section"] not in seen:
            seen.append(e["section"])
    return seen


__all__ = ["QAMatch", "match_qa", "count", "sections"]
