import pytest

from app import prompts
from app.email_sequence import EmailDraftError, EmailSequenceService

_SEQUENCE = {
    "emails": [
        {"position": 1, "goal": "deliver", "timing": "immediately", "subject": "Your guide, {{name}}", "body": "Hi {{name}}, here it is."},
        {"position": 2, "goal": "educate", "timing": "day_3", "subject": "One more thing", "body": "Some education."},
        {"position": 3, "goal": "soft_pitch", "timing": "day_7", "subject": "A quick story", "body": "Case study + soft CTA."},
    ]
}


class FakeEmailLLM:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        self.calls.append((system, user))
        return self._response


class FakeCampaignStore:
    def __init__(self):
        self.campaigns = []
        self.emails = []
        self._next = 0

    async def insert_campaign(self, run_id, lead_id):
        self._next += 1
        self.campaigns.append((self._next, run_id, lead_id))
        return self._next

    async def insert_campaign_email(self, campaign_id, position, goal, timing, subject, body):
        self.emails.append((campaign_id, position, goal, timing, subject, body))


def _service(llm, store):
    return EmailSequenceService(
        llm_client=llm,
        store=store,
        model="claude-sonnet-4-6",
        temperature=0.7,
        max_tokens=4000,
        sender_name="The Cadence Team",
    )


async def test_drafts_three_email_sequence():
    llm = FakeEmailLLM(_SEQUENCE)
    store = FakeCampaignStore()

    campaign_id = await _service(llm, store).draft("RUN-1", 42, "cold chain gaps", "5 Ways to Protect Your Cold Chain")

    assert campaign_id == 1
    assert store.campaigns == [(1, "RUN-1", 42)]
    assert len(store.emails) == 3
    assert [e[1] for e in store.emails] == [1, 2, 3]  # positions
    assert [e[2] for e in store.emails] == ["deliver", "educate", "soft_pitch"]  # goals

    # prompt was interpolated with pain point, guide title, and sender
    _, user = llm.calls[0]
    assert "cold chain gaps" in user
    assert "5 Ways to Protect Your Cold Chain" in user
    assert "The Cadence Team" in user
    assert prompts.EMAIL_SYSTEM  # system prompt present


async def test_empty_sequence_raises():
    store = FakeCampaignStore()
    with pytest.raises(EmailDraftError):
        await _service(FakeEmailLLM({"emails": []}), store).draft("RUN-1", 1, "p", "g")
    assert store.campaigns == []
