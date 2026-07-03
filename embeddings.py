"""Embeddings for the Phase 3 content uniqueness engine.

The handoff spec allows "embedding (or TF-IDF vector)" for the fuzzy-similarity
check. Anthropic has no embeddings endpoint, so the shipped default is a keyless,
deterministic hashing term-frequency embedder producing a fixed 1536-dim,
L2-normalized vector — which maps exactly onto the schema's `vector(1536)` column
and pgvector's cosine operator. It's lexical, not semantic; swap in a real
semantic `Embedder` later (and lower the threshold) without touching the registry
logic — that's the point of the `Embedder` interface.
"""

import hashlib
import math
import re
from typing import Protocol

EMBEDDING_DIM = 1536

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small English stopword set so common words don't dominate the lexical vector.
_STOPWORDS = frozenset(
    """a an the and or but if then else of to in on at by for with from as is are was
    were be been being this that these those it its it's you your we our they their he
    she his her i me my mine us them not no yes do does did doing have has had having
    will would can could should may might must shall about into over under again more
    most some such only own same so than too very just up down out off here there when
    where why how all any both each few other own which who whom what""".split()
)


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Hash tokens into `dim` buckets (term frequency), then L2-normalize.

    Deterministic across processes (uses MD5, not Python's salted `hash()`), so the
    same text always yields the same vector — required for a stable content registry.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOPWORDS:
                continue
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            inv = 1.0 / norm
            vec = [v * inv for v in vec]
        return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is zero)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
