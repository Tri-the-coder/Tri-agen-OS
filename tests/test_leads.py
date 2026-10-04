import importlib
import tempfile

import pytest

import app.api.slack as slack_api
import app.tools.lead_capture as capture
from app.tools.leads import (
    AUTO_SAVE_THRESHOLD, create_lead, find_phone, lead_exists,
    parse_lead_command, recent_leads, score_lead,
)


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setenv("DB_PATH", tempfile.mktemp(suffix=".db"))
    import app.db.session as session

    importlib.reload(session)
    importlib.reload(__import__("app.tools.leads", fromlist=["x"]))
    session.init_db()
    return session


# --- phone + parsing ------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("call me on 01712345678", "01712345678"),
    ("+8801812345678", "+8801812345678"),
    ("my number is 017-1234-5678", "01712345678"),
    ("no number here", None),
    ("1234", None),
    ("02233445566", None),  # landline, not a mobile prefix
])
def test_phone_detection(text, expected):
    assert find_phone(text) == expected


def test_parse_handles_reordered_fields():
    parsed = parse_lead_command("01712345678 - করিম - মুদি দোকান")
    assert parsed["phone"] == "01712345678"
    assert parsed["name"] == "করিম"
    assert parsed["business_type"] == "মুদি দোকান"


def test_parse_without_phone():
    parsed = parse_lead_command("Rahim - Garments")
    assert parsed["name"] == "Rahim"
    assert parsed["phone"] is None


# --- scoring --------------------------------------------------------------------

def test_scoring_matches_the_rubric():
    assert score_lead({"has_business": True}) == 30
    assert score_lead({"asked_pricing": True}) == 25
    assert score_lead({"phone": "01712345678"}) == 25
    assert score_lead({"buying_intent": True}) == 20
    assert score_lead({"has_business": True, "asked_pricing": True,
                       "phone": "01", "buying_intent": True}) == 100


def test_score_never_exceeds_100():
    assert score_lead({"has_business": 1, "asked_pricing": 1, "phone": "1",
                       "buying_intent": 1}) <= 100


# --- storage --------------------------------------------------------------------

def test_lead_is_persisted_with_a_reference(db):
    lead = create_lead("Rahim", "01712345678", "Garments", "wants trial", 80, "telegram")
    assert lead["lead_ref"].startswith("LD-")
    stored = recent_leads(1)[0]
    assert stored["name"] == "Rahim"
    assert stored["score"] == 80
    assert stored["source"] == "telegram"


def test_duplicate_phone_is_detected(db):
    create_lead("Rahim", "01712345678", "Garments", "", 80, "telegram")
    assert lead_exists("01712345678", "Someone Else") is True
    assert lead_exists("01999999999", "Nobody") is False


def test_legacy_add_lead_still_works(db):
    from app.tools.leads import add_lead

    lead = add_lead("ABC Store, interested in WhatsApp SaaS")
    assert lead["name"] == "ABC Store"


# --- pre-filter -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("আমার একটা গার্মেন্টসের দোকান আছে, দাম কত?", True),
    ("my number is 01812345678", True),
    ("I run a pharmacy, what is the price?", True),
    ("what is our Q4 marketing plan?", False),
    ("hello", False),
    ("", False),
])
def test_pre_filter(text, expected):
    assert capture.looks_like_lead(text) is expected


def test_pre_filter_avoids_a_model_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("an ordinary message must not cost a model call")

    monkeypatch.setattr(capture, "complete", explode)
    assert capture.extract_lead("what is our Q4 marketing plan?") is None


# --- extraction -----------------------------------------------------------------

def _model(content):
    return lambda sp, up: {"model": "test:free", "content": content}


def test_extraction_parses_fenced_json(monkeypatch):
    monkeypatch.setattr(capture, "complete", _model(
        '```json\n{"name":"Karim","business_type":"pharmacy","has_business":true,'
        '"asked_pricing":true,"buying_intent":true,"notes":"wants to start"}\n```'))
    lead = capture.extract_lead("I run a pharmacy, price? my number 01812345678")
    assert lead["name"] == "Karim"
    assert lead["score"] == 100


def test_phone_comes_from_the_text_not_the_model(monkeypatch):
    """A hallucinated number must never reach the database."""
    monkeypatch.setattr(capture, "complete", _model(
        '{"name":"Karim","phone":"01999999999","has_business":true,"asked_pricing":true,'
        '"buying_intent":false,"notes":""}'))
    lead = capture.extract_lead("I run a shop, price? call 01812345678")
    assert lead["phone"] == "01812345678"


def test_unparseable_output_is_survivable(monkeypatch):
    monkeypatch.setattr(capture, "complete", _model("sorry, I cannot do that"))
    assert capture.extract_lead("I run a shop, what is the price?") is None


def test_model_failure_is_survivable(monkeypatch):
    import app.services.models as models

    def boom(*a, **k):
        raise models.AllModelsUnavailable([{"model": "m", "status": 429, "error": "x"}])

    monkeypatch.setattr(capture, "complete", boom)
    assert capture.extract_lead("I run a shop, what is the price?") is None


def test_threshold_gates_auto_save():
    assert capture.should_auto_save({"score": AUTO_SAVE_THRESHOLD}) is True
    assert capture.should_auto_save({"score": AUTO_SAVE_THRESHOLD - 1}) is False
    assert capture.should_auto_save(None) is False


# --- end to end -----------------------------------------------------------------

def test_high_score_lead_is_saved_and_posted(db, monkeypatch):
    posted = {}
    monkeypatch.setattr(capture, "complete", _model(
        '{"name":"Karim","business_type":"pharmacy","has_business":true,'
        '"asked_pricing":true,"buying_intent":true,"notes":"wants to start"}'))
    monkeypatch.setattr(slack_api.slack, "post_lead",
                        lambda lead: posted.update(lead) or {"ok": True})

    saved = slack_api.capture_lead_from("I run a pharmacy, price? call 01812345678", "slack")
    assert saved is not None
    assert posted["name"] == "Karim"
    assert recent_leads(1)[0]["name"] == "Karim"


def test_low_score_lead_is_not_saved(db, monkeypatch):
    monkeypatch.setattr(capture, "complete", _model(
        '{"name":null,"business_type":"shop","has_business":true,'
        '"asked_pricing":false,"buying_intent":false,"notes":""}'))
    assert slack_api.capture_lead_from("I have a shop, what is the price?", "slack") is None
    assert recent_leads(1) == []


def test_duplicate_is_not_saved_twice(db, monkeypatch):
    monkeypatch.setattr(capture, "complete", _model(
        '{"name":"Karim","business_type":"pharmacy","has_business":true,'
        '"asked_pricing":true,"buying_intent":true,"notes":""}'))
    monkeypatch.setattr(slack_api.slack, "post_lead", lambda lead: {"ok": True})

    msg = "I run a pharmacy, price? call 01812345678"
    assert slack_api.capture_lead_from(msg, "slack") is not None
    assert slack_api.capture_lead_from(msg, "slack") is None
    assert len(recent_leads(10)) == 1


def test_slack_post_failure_still_saves_the_lead(db, monkeypatch):
    monkeypatch.setattr(capture, "complete", _model(
        '{"name":"Karim","business_type":"pharmacy","has_business":true,'
        '"asked_pricing":true,"buying_intent":true,"notes":""}'))
    monkeypatch.setattr(slack_api.slack, "post_lead",
                        lambda lead: {"ok": False, "error": "not_in_channel"})

    saved = slack_api.capture_lead_from("pharmacy, price? call 01812345678", "slack")
    assert saved is not None, "a Slack failure must not lose the lead"
    assert recent_leads(1)[0]["name"] == "Karim"


def test_capture_never_raises(db, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(capture, "extract_lead", boom)
    assert slack_api.capture_lead_from("anything", "slack") is None
