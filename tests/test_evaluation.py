from pathlib import Path

from app.ai import Analysis, AnalysisError
from evaluation.run import evaluate, load_cases
from tests.conftest import DRAFT


def test_dataset_has_ten_development_and_twenty_evaluation_cases():
    path = Path(__file__).resolve().parents[1] / "evaluation" / "cases.jsonl"
    dev, evaluation = load_cases(path, "dev"), load_cases(path, "eval")
    assert len(dev) == 10 and len(evaluation) == 20
    assert {case["request_id"] for case in dev}.isdisjoint(case["request_id"] for case in evaluation)
    assert {case["expected_category"] for case in evaluation} == {"incidente", "solicitud", "consulta"}


def test_evaluation_counts_errors_in_denominator_and_does_not_send_gold_labels():
    seen = []

    def analyzer(submission):
        seen.append(submission.model_dump())
        if submission.request_id == "error":
            raise AnalysisError("model_timeout")
        return Analysis(DRAFT, "fixture-not-ai", 10, 20)

    cases = [{"request_id": request_id, "subject": "Example", "message": "Example request", "expected_category": category}
             for request_id, category in [("correct", "incidente"), ("wrong", "consulta"), ("error", "solicitud")]]
    report = evaluate(cases, analyzer)
    assert report["total"] == 3 and report["schema_valid"] == 2 and report["failed"] == 1
    assert report["accuracy_all_requests"] == 1 / 3
    assert report["per_category"]["consulta"] == {"correct": 0, "total": 1}
    assert report["input_tokens"] == 20 and report["output_tokens"] == 40
    assert all("expected_category" not in item for item in seen)
    assert report["rows"][0]["manual_review"]["faithful_summary"] is None
