"""Run the exported workflows in n8n with explicit local fixtures, then clean up."""
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "tests/compose.smoke.yaml"]
HEADERS = {"X-API-Key": "fixture-api"}
REVIEW_HEADERS = {**HEADERS, "X-Reviewer-Key": "fixture-reviewer"}


def docker(*args):
    subprocess.run([*COMPOSE, *args], cwd=ROOT, check=True, timeout=180)


def wait_for(client, url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Service did not become ready: {url}")


def main():
    try:
        docker("up", "--build", "-d")
        with httpx.Client(timeout=45) as client:
            wait_for(client, "http://127.0.0.1:8002/health")
            wait_for(client, "http://127.0.0.1:5679/healthz")
            intake = "http://127.0.0.1:5679/webhook/automation/requests"
            review = "http://127.0.0.1:5679/webhook/automation/review"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if client.post(intake, json={}, headers=HEADERS).status_code == 422:
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("Published intake webhook not ready")

            def submit(request_id):
                return client.post(intake, json={"request_id": request_id, "subject": "Error de ejemplo", "message": "No puedo descargar el reporte."}, headers=HEADERS)

            def decide(request_id, decision="approve", headers=REVIEW_HEADERS):
                return client.post(review, json={"request_id": request_id, "decision": decision}, headers=headers)

            assert client.post(intake, json={}, headers={}).status_code == 401
            assert submit("workflow-success").status_code == 201
            assert submit("workflow-success").status_code == 200
            assert decide("workflow-success", headers=HEADERS).status_code == 403
            published = decide("workflow-success")
            assert published.status_code == 200, published.text
            assert published.json()["status"] == "published", published.text
            assert decide("workflow-success").json()["should_publish"] is False
            assert len(client.get("http://127.0.0.1:8002/fixture/issues").json()) == 1
            print("n8n: receipt, duplicate, approval, publication and replay passed.")

            assert submit("workflow-rejected").status_code == 201
            assert decide("workflow-rejected", "reject").json()["record"]["status"] == "rejected"
            assert decide("workflow-rejected").status_code == 409
            assert len(client.get("http://127.0.0.1:8002/fixture/issues").json()) == 1
            print("n8n: rejection does not publish.")

            assert submit("workflow-analysis-failure").status_code == 502
            assert decide("workflow-analysis-failure").status_code == 409
            print("n8n: analysis error is propagated and cannot be approved.")

            assert submit("workflow-uncertain").status_code == 201
            uncertain = decide("workflow-uncertain")
            assert uncertain.status_code == 200, uncertain.text
            assert uncertain.json()["status"] == "needs_reconciliation", uncertain.text
            assert decide("workflow-uncertain").json()["should_publish"] is False
            issues = client.get("http://127.0.0.1:8002/fixture/issues").json()
            assert len(issues) == 2, "The uncertain create was retried"
            response = client.post("http://127.0.0.1:8002/requests/workflow-uncertain/reconcile", json={"issue_number": 2}, headers=REVIEW_HEADERS)
            assert response.json()["status"] == "published"
            print("n8n: uncertain publication is not recreated and can be reconciled.")
    except BaseException:
        subprocess.run([*COMPOSE, "logs", "--tail", "60"], cwd=ROOT, timeout=20)
        raise
    finally:
        subprocess.run([*COMPOSE, "down", "--volumes"], cwd=ROOT, timeout=30)


if __name__ == "__main__":
    main()
