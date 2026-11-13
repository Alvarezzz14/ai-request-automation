import json
import subprocess
import os
import sys

import pytest

from app.ai import AnalysisError, OpenCodeAnalyzer, run_cli
from app.models import Submission
from tests.conftest import DRAFT, PAYLOAD

MODEL = "opencode/glm-5.3-flash-free"


def events(text=None):
    return [
        {"type": "text", "part": {"text": text if text is not None else DRAFT.model_dump_json()}},
        {"type": "step_finish", "part": {"tokens": {"input": 10, "output": 20}}},
    ]


def result(items, code=0):
    return subprocess.CompletedProcess([], code, "\n".join(json.dumps(item) for item in items), "")


def test_cli_configuration_isolated_without_tools_or_application_secrets(monkeypatch):
    monkeypatch.setenv("AUTOMATION_API_TOKEN", "must-not-be-inherited")

    def runner(command, *, cwd, env, timeout):
        config = json.loads((cwd / "config" / "opencode" / "opencode.jsonc").read_text())
        assert config["agents"]["classifier"]["steps"] == 1
        assert config["policies"][0] == {"action": "tool.use", "resource": "*", "effect": "deny"}
        assert "--standalone" in command and command[command.index("--model") + 1] == MODEL
        assert "AUTOMATION_API_TOKEN" not in env
        assert env["XDG_CONFIG_HOME"].startswith(str(cwd))
        assert json.loads(command[-1])["request"]["message"] == PAYLOAD["message"]
        assert "request_id" not in json.loads(command[-1])["request"]
        assert timeout == 60
        return result(events())

    analysis = OpenCodeAnalyzer(MODEL, runner)(Submission(**PAYLOAD))
    assert analysis.draft == DRAFT
    assert (analysis.input_tokens, analysis.output_tokens) == (10, 20)


@pytest.mark.parametrize("text", ["not json", "```json\n{}\n```", json.dumps({**DRAFT.model_dump(), "category": "other"}),
                                  json.dumps({**DRAFT.model_dump(), "repository": "attacker/repo"})])
def test_model_output_must_validate(text):
    with pytest.raises(AnalysisError, match="invalid_model_output"):
        OpenCodeAnalyzer(MODEL, lambda *a, **kw: result(events(text)))(Submission(**PAYLOAD))


def test_free_limit_is_not_retried_or_switched_to_paid():
    calls = []

    def runner(*args, **kwargs):
        calls.append(1)
        return result([{"type": "error", "error": {"type": "provider.free-limit", "status": 429}}], 1)

    with pytest.raises(AnalysisError, match="model_rate_limited"):
        OpenCodeAnalyzer(MODEL, runner)(Submission(**PAYLOAD))
    assert len(calls) == 1


def test_missing_cli_is_reported():
    def runner(*args, **kwargs):
        raise FileNotFoundError()
    with pytest.raises(AnalysisError, match="opencode_not_installed"):
        OpenCodeAnalyzer(MODEL, runner)(Submission(**PAYLOAD))


def test_paid_model_is_rejected_before_execution():
    with pytest.raises(AnalysisError, match="model_not_allowed"):
        OpenCodeAnalyzer("openai/paid-model", None)(Submission(**PAYLOAD))


def test_provider_failure_is_sanitized():
    with pytest.raises(AnalysisError, match="model_unavailable"):
        OpenCodeAnalyzer(MODEL, lambda *a, **kw: result([{"type": "error", "error": {"message": "private details"}}], 1))(Submission(**PAYLOAD))


def test_missing_usage_remains_unknown():
    analysis = OpenCodeAnalyzer(MODEL, lambda *a, **kw: result(events()[:1]))(Submission(**PAYLOAD))
    assert analysis.input_tokens is None and analysis.output_tokens is None


def test_timeout_terminates_the_spawned_process(tmp_path):
    with pytest.raises(AnalysisError, match="model_timeout"):
        run_cli([sys.executable, "-c", "import threading; threading.Event().wait()"],
                cwd=tmp_path, env=os.environ.copy(), timeout=0.1)
