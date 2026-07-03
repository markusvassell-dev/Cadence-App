from app.uniqueness import is_too_similar, max_similarity, similarity


def test_identical_strings_are_maximally_similar():
    assert similarity("cold chain gaps for probiotics", "cold chain gaps for probiotics") == 1.0


def test_paraphrase_scores_high():
    a = "Rural clinics lack reliable cold chain storage for probiotics"
    b = "Probiotics spoil because rural clinics have no reliable cold chain storage"
    # token-set ratio is order/duplicate-insensitive, so heavy word overlap -> high score
    assert similarity(a, b) > 0.70


def test_distinct_pain_points_score_low():
    a = "Rural clinics lack reliable cold chain storage for probiotics"
    b = "Maternal anemia goes undiagnosed due to a shortage of point-of-care hemoglobin tests"
    assert similarity(a, b) < 0.70


def test_max_similarity_empty_existing_is_zero():
    assert max_similarity("anything", []) == 0.0


def test_is_too_similar_uses_threshold():
    existing = ["Rural clinics lack reliable cold chain storage for probiotics"]
    near_dup = "Probiotics spoil because rural clinics have no reliable cold chain storage"
    distinct = "Maternal anemia goes undiagnosed without point-of-care hemoglobin tests"

    assert is_too_similar(near_dup, existing, threshold=0.70) is True
    assert is_too_similar(distinct, existing, threshold=0.70) is False
