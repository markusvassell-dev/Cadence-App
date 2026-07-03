from app.activecampaign import StubActiveCampaign, _split_name, build_ac_client
from app.config import Settings


def test_split_name():
    assert _split_name("Ada Lovelace") == ("Ada", "Lovelace")
    assert _split_name("Cher") == ("Cher", "")
    assert _split_name("  Jean  Luc  Picard ") == ("Jean", "Luc Picard")
    assert _split_name("") == ("", "")


async def test_stub_returns_deterministic_contact_id():
    stub = StubActiveCampaign()
    a = await stub.sync_contact(name="Ada", email="ada@example.com", lead_source="RUN-1", pain_point="x")
    b = await stub.sync_contact(name="Ada", email="ADA@example.com", lead_source="RUN-2", pain_point="y")
    assert a.startswith("stub-")
    assert a == b  # keyed on lowercased email


def test_build_ac_client_selects_stub_without_creds():
    assert isinstance(build_ac_client(Settings()), StubActiveCampaign)


def test_build_ac_client_selects_http_with_creds():
    from app.activecampaign import HttpActiveCampaign

    s = Settings(ac_base_url="https://acct.api-us1.com", ac_api_token="token")
    assert isinstance(build_ac_client(s), HttpActiveCampaign)
