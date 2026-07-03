import pytest

from app.leads import LeadValidationError, validate_lead


def test_valid_lead_is_trimmed():
    name, email = validate_lead("  Ada Lovelace  ", "  ada@example.com ")
    assert name == "Ada Lovelace"
    assert email == "ada@example.com"


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_name_rejected(name):
    with pytest.raises(LeadValidationError, match="name"):
        validate_lead(name, "ada@example.com")


@pytest.mark.parametrize(
    "email",
    ["", "not-an-email", "missing@domain", "@example.com", "a b@example.com", None],
)
def test_bad_email_rejected(email):
    with pytest.raises(LeadValidationError, match="email"):
        validate_lead("Ada", email)
