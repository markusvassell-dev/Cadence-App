"""Pain-point uniqueness guard (Phase 2).

String-similarity check using token-set ratio (handoff README Phase 2: discard a
new pain point if similarity > 0.70 to any existing one). Pure functions so they
can be unit-tested without a DB or LLM. The harder embedding/cosine uniqueness for
generated *content* is Phase 3 (the content_registry).
"""

from rapidfuzz import fuzz


def similarity(a: str, b: str) -> float:
    """Token-set ratio, normalized to 0.0-1.0. Order- and duplicate-insensitive,
    which suits comparing paraphrased one-sentence pain points."""
    return fuzz.token_set_ratio(a, b) / 100.0


def max_similarity(text: str, existing: list[str]) -> float:
    return max((similarity(text, e) for e in existing), default=0.0)


def is_too_similar(text: str, existing: list[str], threshold: float) -> bool:
    """True if `text` is closer than `threshold` to any existing pain point."""
    return max_similarity(text, existing) > threshold
