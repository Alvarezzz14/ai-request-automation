from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock

import pytest
from fastapi.testclient import TestClient

from app.ai import Analysis, AnalysisError
from app.main import create_app
from tests.conftest import DRAFT, HEADERS, PAYLOAD, REVIEW_HEADERS


def receive(client):
    return client.post("/requests", json=PAYLOAD, headers=HEADERS)


def decide(client, decision="approve", headers=REVIEW_HEADERS):
    return client.post("/requests/request-001/decision", json={"decision": decision}, headers=headers)


def test_receipt_and_duplicate_use_one_analysis(settings):
    calls = []
    client = TestClient(create_app(settings, lambda item: calls.append(item) or Analysis(DRAFT, "test-model", 10, 5)))
    first, second = receive(client), receive(client)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1
    assert first.json()["status"] == "pending_review"
    assert first.json()["metrics"]["input_tokens"] == 10
    assert "claim_token" not in str(first.json())
    assert client.get("/requests/request-001", headers=HEADERS).json() == first.json()


def test_same_id_with_changed_content_conflicts(client):
    receive(client)
    response = client.post("/requests", json={**PAYLOAD, "message": "Otro mensaje"}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"] == "request_id_conflict"


def test_normalized_whitespace_replays_same_request(client):
    receive(client)
    response = client.post("/requests", json={**PAYLOAD, "subject": " Error en reporte "}, headers=HEADERS)
    assert response.status_code == 200


@pytest.mark.parametrize("change", [
    {"request_id": "bad/id"}, {"message": " "}, {"subject": "x" * 201},
    {"message": "x" * 8001}, {"request_id": 1}, {"repository": "attacker/repo"},
])
def test_invalid_receipts_are_rejected(client, change):
    assert client.post("/requests", json={**PAYLOAD, **change}, headers=HEADERS).status_code == 422


def test_requests_require_api_authentication(client):
    assert client.post("/requests", json=PAYLOAD).status_code == 401
    assert client.get("/requests/request-001").status_code == 401


def test_decision_requires_reviewer_and_does_not_claim_on_failure(client):
    receive(client)
    assert decide(client, headers=HEADERS).status_code == 403
    assert decide(client).json()["should_publish"] is True


def test_invalid_decision_or_modified_draft_is_rejected(client):
    receive(client)
    for body in [{"decision": "maybe"}, {"decision": "approve", "title": "Override"}]:
        assert client.post("/requests/request-001/decision", json=body, headers=REVIEW_HEADERS).status_code == 422


def test_approval_claims_once_and_uses_fixed_destination_and_stored_draft(client):
    receive(client)
    first = decide(client).json()
    assert first["should_publish"] is True
    assert first["publication"]["owner"] == "test-owner"
    assert first["publication"]["repository"] == "demo"
    assert first["publication"]["title"] == DRAFT.title
    assert "automation-request:request-001" in first["publication"]["body"]
    second = decide(client).json()
    assert second["should_publish"] is False
    assert "publication" not in second
    assert second["record"]["status"] == "publishing"


def test_concurrent_approvals_grant_exactly_one_publication(client):
    receive(client)
    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(lambda _: decide(client), range(12)))
    assert all(response.status_code == 200 for response in responses)
    assert sum(response.json()["should_publish"] for response in responses) == 1


def test_rejection_is_idempotent_and_cannot_be_approved(client):
    receive(client)
    first, second = decide(client, "reject"), decide(client, "reject")
    assert first.status_code == second.status_code == 200
    assert second.json()["record"]["status"] == "rejected"
    assert second.json()["should_publish"] is False
    assert decide(client).status_code == 409


def test_approved_request_cannot_be_rejected(client):
    receive(client)
    decide(client)
    assert decide(client, "reject").status_code == 409


def test_success_confirmation_and_duplicate_are_idempotent(client):
    receive(client)
    claim = decide(client).json()["publication"]["claim_token"]
    body = {"claim_token": claim, "issue_number": 7}
    url = "/requests/request-001/publication-success"
    first, second = client.post(url, json=body, headers=HEADERS), client.post(url, json=body, headers=HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["issue_url"] == "https://github.com/test-owner/demo/issues/7"
    assert first.json()["status"] == "published"
    assert decide(client).json()["should_publish"] is False
    assert client.post(url, json={**body, "issue_number": 8}, headers=HEADERS).status_code == 409


def test_wrong_claim_cannot_confirm_or_mark_failure(client):
    receive(client)
    decide(client)
    assert client.post("/requests/request-001/publication-success", json={"claim_token": "wrong", "issue_number": 1}, headers=HEADERS).status_code == 403
    assert client.post("/requests/request-001/publication-failure", json={"claim_token": "wrong"}, headers=HEADERS).status_code == 403


def test_uncertain_publication_is_not_retried_and_can_be_reconciled(client):
    receive(client)
    claim = decide(client).json()["publication"]["claim_token"]
    url = "/requests/request-001/publication-failure"
    for _ in range(2):
        response = client.post(url, json={"claim_token": claim}, headers=HEADERS)
        assert response.json()["status"] == "needs_reconciliation"
    assert decide(client).json()["should_publish"] is False
    reconcile = "/requests/request-001/reconcile"
    assert client.post(reconcile, json={"issue_number": 4}, headers=HEADERS).status_code == 403
    response = client.post(reconcile, json={"issue_number": 4}, headers=REVIEW_HEADERS)
    assert response.json()["status"] == "published"
    assert response.json()["issue_url"].endswith("/issues/4")
    assert client.post(reconcile, json={"issue_number": 4}, headers=REVIEW_HEADERS).status_code == 200


def test_lost_callback_can_be_reconciled_without_reissuing(client):
    receive(client)
    decide(client)
    assert decide(client).json()["should_publish"] is False
    response = client.post("/requests/request-001/reconcile", json={"issue_number": 9}, headers=REVIEW_HEADERS)
    assert response.json()["status"] == "published"


def test_late_confirmation_recovers_uncertain_publication_and_failure_cannot_downgrade(client):
    receive(client)
    claim = decide(client).json()["publication"]["claim_token"]
    client.post("/requests/request-001/publication-failure", json={"claim_token": claim}, headers=HEADERS)
    client.post("/requests/request-001/publication-success", json={"claim_token": claim, "issue_number": 8}, headers=HEADERS)
    response = client.post("/requests/request-001/publication-failure", json={"claim_token": claim}, headers=HEADERS)
    assert response.json()["status"] == "published"
    assert response.json()["issue_url"].endswith("/issues/8")


def test_reconciliation_cannot_publish_an_unapproved_draft(client):
    receive(client)
    assert client.post("/requests/request-001/reconcile", json={"issue_number": 1}, headers=REVIEW_HEADERS).status_code == 409


def test_missing_repository_does_not_consume_approval(settings):
    client = TestClient(create_app(replace(settings, github_repository=""), lambda _: Analysis(DRAFT, "test")))
    receive(client)
    assert decide(client).status_code == 503
    assert client.get("/requests/request-001", headers=HEADERS).json()["status"] == "pending_review"


def test_missing_request_returns_404(client):
    assert client.get("/requests/missing", headers=HEADERS).status_code == 404
    assert client.post("/requests/missing/decision", json={"decision": "approve"}, headers=REVIEW_HEADERS).status_code == 404


def test_failure_is_persisted_not_retried_by_receipt_and_can_be_retried_explicitly(settings):
    calls = []

    def analyzer(_):
        calls.append(1)
        if len(calls) == 1:
            raise AnalysisError("model_timeout")
        return Analysis(DRAFT, "test")

    client = TestClient(create_app(settings, analyzer))
    first = receive(client)
    assert first.status_code == 502
    assert first.json()["error_code"] == "model_timeout"
    assert receive(client).status_code == 200
    assert len(calls) == 1
    assert decide(client).status_code == 409
    url = "/requests/request-001/retry-analysis"
    assert client.post(url, headers=HEADERS).status_code == 403
    assert client.post(url, headers=REVIEW_HEADERS).json()["status"] == "pending_review"
    assert client.post(url, headers=REVIEW_HEADERS).status_code == 409
    assert len(calls) == 2


def test_concurrent_duplicates_do_not_repeat_inflight_analysis(settings):
    entered, release = Event(), Event()
    calls, lock = [], Lock()

    def analyzer(_):
        with lock:
            calls.append(1)
        entered.set()
        assert release.wait(10)
        return Analysis(DRAFT, "test")

    client = TestClient(create_app(settings, analyzer))
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(receive, client)
        try:
            assert entered.wait(5)
            duplicates = list(pool.map(lambda _: receive(client), range(12)))
            assert all(response.status_code == 202 for response in duplicates)
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 201


def test_restart_keeps_publication_claim_and_does_not_reanalyze(settings):
    client = TestClient(create_app(settings, lambda _: Analysis(DRAFT, "test")))
    receive(client)
    decide(client)

    def unexpected(_):
        raise AssertionError("Should not call AI again")

    restarted = TestClient(create_app(settings, unexpected))
    assert receive(restarted).json()["status"] == "publishing"
    assert decide(restarted).json()["should_publish"] is False


def test_exception_details_are_not_returned_or_persisted(settings):
    def analyzer(_):
        raise RuntimeError("secret-api-key-and-source-message")

    client = TestClient(create_app(settings, analyzer))
    response = receive(client)
    assert response.status_code == 502
    assert response.json()["error_code"] == "analysis_internal_error"
    assert "secret-api-key" not in response.text


def test_no_credentials_cannot_authorize_requests(settings):
    client = TestClient(create_app(replace(settings, api_token=""), lambda _: Analysis(DRAFT, "test")))
    assert receive(client).status_code == 503
