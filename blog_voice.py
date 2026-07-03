"""Deterministic post-generation voice check for blog drafts (addendum).

Pure string work — no model call — so it's cheap to run on every draft.
`validate_blog_voice()` returns a list of human-readable problems; an empty list
means the draft passes. Wired into ContentService._ensure_unique as the optional
`validate` callback: a non-empty result triggers a regenerate, exactly like a
uniqueness collision.
"""

# Hard cap on em-dashes — the single biggest "this was written by AI" tell.
MAX_EM_DASHES = 2

# Phrases that read as AI filler. Lowercase; matched case-insensitively as substrings.
BANNED_PHRASES = (
    "delve into",
    "navigating the landscape",
    "navigate the landscape",
    "it's important to note",
    "it is important to note",
    "in today's fast-paced",
    "in the ever-evolving",
    "when it comes to",
    "a testament to",
    "tapestry",
)


def count_em_dashes(text: str) -> int:
    # U+2014 em dash. (Not counting hyphens or en dashes.)
    return text.count("—")


def validate_blog_voice(text: str) -> list[str]:
    """Return a list of voice problems with `text`. Empty list == passes."""
    problems: list[str] = []

    em = count_em_dashes(text)
    if em > MAX_EM_DASHES:
        problems.append(f"{em} em-dashes (max {MAX_EM_DASHES})")

    low = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            problems.append(f'banned phrase: "{phrase}"')

    return problems
