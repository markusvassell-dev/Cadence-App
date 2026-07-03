import pytest

from app.blog_voice import MAX_EM_DASHES, validate_blog_voice


def test_clean_draft_passes():
    text = "You know the feeling. A cold chain fails, and the medicine spoils. Here's what helps."
    assert validate_blog_voice(text) == []


def test_two_em_dashes_pass_but_three_fail():
    two = "One clause — a second — and done."  # exactly MAX_EM_DASHES
    three = "One — two — three — too many."
    assert MAX_EM_DASHES == 2
    assert validate_blog_voice(two) == []
    problems = validate_blog_voice(three)
    assert any("em-dash" in p for p in problems)


@pytest.mark.parametrize("phrase", ["delve into", "Delve Into", "navigating the landscape", "It's Important To Note", "tapestry"])
def test_banned_phrases_flagged_case_insensitively(phrase):
    text = f"Some intro. We should {phrase} the details. Some outro."
    problems = validate_blog_voice(text)
    assert any("banned phrase" in p for p in problems)


def test_multiple_problems_all_reported():
    text = "Let's delve into this — and — really — navigating the landscape here."
    problems = validate_blog_voice(text)
    assert any("em-dash" in p for p in problems)
    assert any("delve into" in p for p in problems)
    assert any("navigating the landscape" in p for p in problems)
