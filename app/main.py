import hmac
import json
import logging
import re
import shutil
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .ai import AnalysisError, OpenCodeAnalyzer, PROMPT_VERSION
from .config import Settings
from .models import Decision, Draft, PublicationFailure, PublicationSuccess, Reconciliation, Submission
from .store import StateError, Store, public_record

logger = logging.getLogger("automation")


def create_app(settings: Settings | None = None, analyzer=None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = settings or Settings.from_env()
    if settings.github_repository and not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", settings.github_repository):
        raise ValueError("GITHUB_REPOSITORY must be owner/repository")
    store = Store(settings.database_path)
    analyze = analyzer or OpenCodeAnalyzer(settings.opencode_model)

    def authenticate(x_api_key: Annotated[str, Header()] = ""):
        if not settings.api_token:
            raise HTTPException(503, "api_token_not_configured")
        if not hmac.compare_digest(settings.api_token.encode(), x_api_key.encode()):
            raise HTTPException(401, "invalid_api_token")

    def reviewer(x_reviewer_key: Annotated[str, Header()] = ""):
        if not settings.reviewer_token:
            raise HTTPException(503, "reviewer_not_configured")
        if not hmac.compare_digest(settings.reviewer_token.encode(), x_reviewer_key.encode()):
            raise HTTPException(403, "invalid_reviewer_token")

    app = FastAPI(title="AI Request Automation", version="0.1.0")

    @app.exception_handler(StateError)
    async def state_error(_request: Request, error: StateError):
        return JSONResponse(status_code=error.status, content={"detail": error.code})

    def run_analysis(submission):
        start = time.perf_counter()
        metrics = {"prompt_version": PROMPT_VERSION, "model": settings.opencode_model,
                   "input_tokens": None, "output_tokens": None}
        draft, error_code = None, None
        try:
            result = analyze(submission)
            draft = Draft.model_validate(result.draft).model_dump()
            metrics.update(model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
        except AnalysisError as error:
            error_code = error.code
        except ValidationError:
            error_code = "invalid_model_output"
        except Exception:
            # Do not persist provider exceptions, which may contain credentials or content.
            error_code = "analysis_internal_error"
        metrics["duration_ms"] = round((time.perf_counter() - start) * 1000, 3)
        store.finish_analysis(submission.request_id, draft, metrics, error_code)
        logger.info(json.dumps({"request_id": submission.request_id, "phase": "analysis",
                                "duration_ms": metrics["duration_ms"], "error_code": error_code}))
        return public_record(store.get(submission.request_id)), bool(error_code)

    @app.get("/health")
    def health():
        return {"status": "ok", "opencode_installed": bool(shutil.which("opencode")), "model": settings.opencode_model}

    @app.post("/requests", dependencies=[Depends(authenticate)])
    def receive(submission: Submission):
        if not store.receive(submission):
            record = public_record(store.get(submission.request_id))
            return JSONResponse(status_code=202 if record["status"] == "analyzing" else 200, content=record)
        record, failed = run_analysis(submission)
        return JSONResponse(status_code=502 if failed else 201, content=record)

    @app.get("/requests/{request_id}", dependencies=[Depends(authenticate)])
    def detail(request_id: str):
        return public_record(store.get(request_id))

    @app.post("/requests/{request_id}/retry-analysis", dependencies=[Depends(authenticate), Depends(reviewer)])
    def retry_analysis(request_id: str):
        record, failed = run_analysis(store.claim_analysis_retry(request_id))
        return JSONResponse(status_code=502 if failed else 200, content=record)

    @app.post("/requests/{request_id}/decision", dependencies=[Depends(authenticate), Depends(reviewer)])
    def decide(request_id: str, body: Decision):
        publication = store.decide(request_id, body.decision, settings.github_repository)
        record = public_record(store.get(request_id))
        response = {"record": record, "should_publish": publication is not None}
        if publication:
            draft = publication["draft"]
            questions = "\n".join(f"- {item}" for item in draft["missing_information"]) or "Sin datos adicionales solicitados."
            owner, repo = publication["repository"].split("/")
            response["publication"] = {
                "claim_token": publication["claim_token"], "owner": owner, "repository": repo,
                "title": draft["title"],
                "body": f"Categoría: {draft['category']}\n\n{draft['summary']}\n\n### Información pendiente\n{questions}\n\n<!-- automation-request:{request_id} -->",
            }
        return response

    @app.post("/requests/{request_id}/publication-success", dependencies=[Depends(authenticate)])
    def publication_success(request_id: str, body: PublicationSuccess):
        store.finish_publication(request_id, body.claim_token, body.issue_number)
        return public_record(store.get(request_id))

    @app.post("/requests/{request_id}/publication-failure", dependencies=[Depends(authenticate)])
    def publication_failure(request_id: str, body: PublicationFailure):
        store.finish_publication(request_id, body.claim_token)
        return public_record(store.get(request_id))

    @app.post("/requests/{request_id}/reconcile", dependencies=[Depends(authenticate), Depends(reviewer)])
    def reconcile(request_id: str, body: Reconciliation):
        store.reconcile(request_id, body.issue_number)
        return public_record(store.get(request_id))

    return app
