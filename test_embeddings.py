import math

from app.embeddings import EMBEDDING_DIM, HashingEmbedder, cosine_similarity


def test_embedding_has_schema_dimension_and_is_normalized():
    emb = HashingEmbedder().embed("rural clinics lack reliable cold chain storage")
    assert len(emb) == EMBEDDING_DIM
    norm = math.sqrt(sum(x * x for x in emb))
    assert norm == 0.0 or abs(norm - 1.0) < 1e-9


def test_deterministic():
    e = HashingEmbedder()
    assert e.embed("same text here") == e.embed("same text here")


def test_identical_text_has_cosine_one():
    e = HashingEmbedder()
    v = e.embed("maternal anemia goes undiagnosed without hemoglobin tests")
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_distinct_topics_score_below_identical():
    e = HashingEmbedder()
    a = e.embed("cold chain storage failures spoil vaccines in remote clinics")
    b = e.embed("financial literacy workshops for smallholder farmers in cities")
    assert cosine_similarity(a, b) < 0.5


def test_empty_text_embeds_to_zero_vector():
    emb = HashingEmbedder().embed("")
    assert all(x == 0.0 for x in emb)
    assert cosine_similarity(emb, emb) == 0.0
