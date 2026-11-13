"""Explicit real-provider evaluation; never runs as part of pytest."""
import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.ai import AnalysisError, OpenCodeAnalyzer, PROMPT_VERSION
from app.models import Draft, Submission


def load_cases(path: Path, split: str):
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len({case["request_id"] for case in cases}) != len(cases):
        raise ValueError("Dataset contains duplicate identifiers")
    return [case for case in cases if case["split"] == split]


def evaluate(cases, analyzer):
    rows = []
    for case in cases:
        submission = Submission.model_validate({key: case[key] for key in ["request_id", "subject", "message"]})
        start = time.perf_counter()
        row = {"request_id": submission.request_id, "expected_category": case["expected_category"],
               "draft": None, "error_code": None, "model": None,
               "input_tokens": None, "output_tokens": None,
               "manual_review": {"faithful_summary": None, "invented_facts": None, "useful_missing_information": None}}
        try:
            result = analyzer(submission)
            row.update(draft=Draft.model_validate(result.draft).model_dump(), model=result.model,
                       input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        except AnalysisError as error:
            row["error_code"] = error.code
        row["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)
        rows.append(row)
    correct = Counter(row["expected_category"] for row in rows
                      if row["draft"] and row["draft"]["category"] == row["expected_category"])
    totals = Counter(row["expected_category"] for row in rows)
    valid = sum(row["draft"] is not None for row in rows)
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(), "prompt_version": PROMPT_VERSION,
        "total": len(rows), "schema_valid": valid, "failed": len(rows) - valid,
        "accuracy_all_requests": sum(correct.values()) / len(rows) if rows else None,
        "per_category": {category: {"correct": correct[category], "total": total} for category, total in totals.items()},
        "input_tokens": sum(row["input_tokens"] or 0 for row in rows) if any(row["input_tokens"] is not None for row in rows) else None,
        "output_tokens": sum(row["output_tokens"] or 0 for row in rows) if any(row["output_tokens"] is not None for row in rows) else None,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate a real free OpenCode model; subject to provider limits.")
    parser.add_argument("--split", choices=["dev", "eval"], default="eval")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = os.getenv("OPENCODE_MODEL", "opencode/glm-5.3-flash-free")
    cases = load_cases(Path(__file__).with_name("cases.jsonl"), args.split)
    report = evaluate(cases, OpenCodeAnalyzer(model))
    report.update(split=args.split, configured_model=model, provider="opencode-cli")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
