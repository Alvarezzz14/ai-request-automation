import pytest
from fastapi.testclient import TestClient

from app.ai import Analysis
from app.config import Settings
from app.main import create_app
from app.models import Draft

PAYLOAD = {"request_id": "request-001", "subject": "Error en reporte", "message": "No puedo descargar el reporte."}
HEADERS = {"X-API-Key": "test-api-token"}
REVIEW_HEADERS = {**HEADERS, "X-Reviewer-Key": "test-reviewer-token"}
DRAFT = Draft(category="incidente", title="Error al descargar reporte",
              summary="La persona no puede descargar el reporte.", missing_information=["¿Qué error aparece?"])


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / "requests.sqlite3"), api_token="test-api-token",
                    reviewer_token="test-reviewer-token", github_repository="test-owner/demo", opencode_model="test-model")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings, analyzer=lambda _: Analysis(DRAFT, "test-model", 100, 50)))
