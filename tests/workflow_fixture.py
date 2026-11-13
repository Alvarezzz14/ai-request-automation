"""Local-only deterministic services for n8n integration checks, not real AI."""
from fastapi import Header, HTTPException

from app.ai import Analysis, AnalysisError
from app.config import Settings
from app.main import create_app as application
from app.models import Draft


def create_app():
    def analyzer(submission):
        if submission.request_id == "workflow-analysis-failure":
            raise AnalysisError("model_timeout")
        return Analysis(Draft(category="incidente", title=f"Fixture: {submission.subject}",
                              summary=submission.message, missing_information=[]), "fixture-not-ai", 0, 0)

    app = application(Settings(database_path="/tmp/workflow-fixture.sqlite3", api_token="fixture-api",
                               reviewer_token="fixture-reviewer", github_repository="test-owner/demo"), analyzer)
    issues = []

    @app.post("/repos/test-owner/demo/issues")
    def create_issue(body: dict, authorization: str = Header(default="")):
        if authorization != "token not-a-real-token":
            raise HTTPException(401, "invalid_fixture_credential")
        number = len(issues) + 1
        issue = {"number": number, "html_url": f"https://github.com/test-owner/demo/issues/{number}", **body}
        issues.append(issue)
        if "automation-request:workflow-uncertain" in body.get("body", ""):
            raise HTTPException(503, "Simulates remote creation followed by an uncertain response")
        return issue

    @app.get("/fixture/issues")
    def fixture_issues():
        return issues

    return app
