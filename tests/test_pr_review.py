import pytest

import app.api.tasks as tasks_api
import app.tools.pr_review as pr_review

PR = {
    "number": 7, "title": "Add caching", "author": "rafi",
    "url": "https://github.com/x/y/pull/7",
    "files_changed": 2, "additions": 40, "deletions": 3,
    "diff": "--- a/app.py\n+++ b/app.py\n+def cache(): pass\n",
}


def _model(text):
    return lambda sp, up: {"model": "test:free", "content": text}


def _client(monkeypatch):
    monkeypatch.setenv("HEALTH_TOKEN", "cron-token")
    import app.main as main

    return main.create_app(testing=True).test_client()


# --- prompt guarantees ----------------------------------------------------------

def test_prompt_forbids_claiming_things_it_cannot_see():
    p = pr_review.REVIEW_PROMPT
    assert "Only the diff below" in p
    assert "Never say tests pass or fail" in p
    assert "Never invent a file name" in p


def test_prompt_allows_an_empty_result():
    assert "An empty finding list is a valid" in pr_review.REVIEW_PROMPT


def test_large_diff_is_truncated_and_flagged():
    big = dict(PR, diff="x" * (pr_review.MAX_DIFF_CHARS + 5000))
    context = pr_review.build_context(big)
    assert "truncated" in context
    assert len(context) < pr_review.MAX_DIFF_CHARS + 1000


def test_small_diff_is_not_flagged():
    assert "truncated" not in pr_review.build_context(PR)


# --- review behaviour -----------------------------------------------------------

def test_empty_diff_is_rejected_without_a_model_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("an empty diff must not cost a model call")

    monkeypatch.setattr(pr_review, "complete", explode)
    result = pr_review.review(dict(PR, diff="   "))
    assert result["ok"] is False
    assert result["error"] == "empty diff"


def test_rate_limited_models_are_survivable(monkeypatch):
    import app.services.models as models

    def boom(*a, **k):
        raise models.AllModelsUnavailable([{"model": "m", "status": 429, "error": "x"}])

    monkeypatch.setattr(pr_review, "complete", boom)
    result = pr_review.review(PR)
    assert result["ok"] is False
    assert result["rate_limited"] is True


def test_review_failure_says_nothing_was_checked():
    text = pr_review.format_for_slack(PR, {"ok": False, "error": "boom"})
    assert "did not run" in text
    assert "no review" in text


def test_successful_review_carries_its_caveat():
    text = pr_review.format_for_slack(PR, {"ok": True, "model": "m:free", "text": "LGTM"})
    assert "LGTM" in text
    assert "from the diff only" in text
    assert "not a substitute" in text


def test_slack_text_links_the_pr():
    text = pr_review.format_for_slack(PR, {"ok": True, "model": "m", "text": "ok"})
    assert "<https://github.com/x/y/pull/7|PR #7: Add caching>" in text


# --- endpoint -------------------------------------------------------------------

def test_endpoint_requires_the_token(monkeypatch):
    assert _client(monkeypatch).post("/tasks/pr-review", json=PR).status_code == 403


def test_endpoint_rejects_a_missing_pr_number(monkeypatch):
    r = _client(monkeypatch).post("/tasks/pr-review?token=cron-token", json={"diff": "x"})
    assert r.status_code == 400


def test_dry_run_does_not_post(monkeypatch):
    monkeypatch.setattr(pr_review, "complete", _model("LGTM — small and safe."))

    def explode(*a, **k):
        raise AssertionError("a dry run must not post to Slack")

    monkeypatch.setattr(tasks_api.slack, "post_message", explode)
    body = _client(monkeypatch).post("/tasks/pr-review?token=cron-token&dry=1", json=PR).get_json()
    assert body["posted"] is False
    assert "LGTM" in body["preview"]


def test_review_is_posted_to_the_dev_channel(monkeypatch):
    monkeypatch.setattr(pr_review, "complete", _model("NEEDS WORK — unbounded loop."))
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(channel=ch, text=text) or {"ok": True})

    body = _client(monkeypatch).post("/tasks/pr-review?token=cron-token", json=PR).get_json()
    assert body["posted"] is True
    assert body["reviewed"] is True
    assert sent["channel"] == "byabosayee_devs"
    assert "NEEDS WORK" in sent["text"]


def test_custom_dev_channel(monkeypatch):
    monkeypatch.setenv("SLACK_DEV_CHANNEL", "#eng-reviews")
    monkeypatch.setattr(pr_review, "complete", _model("LGTM"))
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(channel=ch) or {"ok": True})

    _client(monkeypatch).post("/tasks/pr-review?token=cron-token", json=PR)
    assert sent["channel"] == "eng-reviews"


def test_slack_failure_is_surfaced(monkeypatch):
    monkeypatch.setattr(pr_review, "complete", _model("LGTM"))
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: {"ok": False, "error": "not_in_channel"})

    body = _client(monkeypatch).post("/tasks/pr-review?token=cron-token", json=PR).get_json()
    assert body["posted"] is False
    assert body["slack_error"] == "not_in_channel"


def test_model_failure_still_posts_a_notice(monkeypatch):
    import app.services.models as models

    def boom(*a, **k):
        raise models.AllModelsUnavailable([{"model": "m", "status": 429, "error": "x"}])

    monkeypatch.setattr(pr_review, "complete", boom)
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(text=text) or {"ok": True})

    body = _client(monkeypatch).post("/tasks/pr-review?token=cron-token", json=PR).get_json()
    assert body["reviewed"] is False
    assert "no review" in sent["text"], "silence would look like approval"


# --- oversized PRs --------------------------------------------------------------

def test_unfetchable_diff_is_treated_as_too_large():
    assert pr_review.too_large({"diff_unavailable": True}) is True


def test_many_files_is_too_large():
    assert pr_review.too_large({"files_changed": pr_review.MAX_FILES_FOR_REVIEW + 1}) is True
    assert pr_review.too_large({"files_changed": pr_review.MAX_FILES_FOR_REVIEW}) is False


def test_bad_file_count_does_not_crash():
    assert pr_review.too_large({"files_changed": "lots"}) is False


def test_oversized_pr_costs_no_model_call(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("an oversized PR must not reach the model")

    monkeypatch.setattr(pr_review, "complete", explode)
    result = pr_review.review({"files_changed": 500, "diff": "x" * 100})
    assert result["skipped"] == "too_large"


def test_oversized_message_does_not_imply_coverage():
    text = pr_review.format_for_slack(
        dict(PR, files_changed=81), {"ok": False, "skipped": "too_large"})
    assert "Too large for automated review" in text
    assert "nothing here has been checked" in text
    assert "splitting" in text


def test_oversized_pr_is_still_posted(monkeypatch):
    sent = {}
    monkeypatch.setattr(tasks_api.slack, "post_message",
                        lambda ch, text: sent.update(text=text) or {"ok": True})

    def explode(*a, **k):
        raise AssertionError("no model call expected")

    monkeypatch.setattr(pr_review, "complete", explode)
    body = _client(monkeypatch).post(
        "/tasks/pr-review?token=cron-token",
        json=dict(PR, files_changed=400, diff_unavailable=True)).get_json()

    assert body["posted"] is True
    assert body["reviewed"] is False
    assert "Too large" in sent["text"], "the team must still hear about the PR"
