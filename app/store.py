import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Submission


class StateError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'analyzing', 'analysis_failed', 'pending_review', 'rejected',
                    'publishing', 'published', 'needs_reconciliation')),
                draft TEXT,
                metrics TEXT,
                error_code TEXT,
                repository TEXT,
                claim_token TEXT,
                issue_number INTEGER,
                created_at TEXT NOT NULL
            )""")

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _row(self, connection, request_id):
        row = connection.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise StateError("request_not_found", 404)
        return dict(row)

    def get(self, request_id):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            return self._row(connection, request_id)
        finally:
            connection.close()

    def receive(self, submission: Submission):
        content = json.dumps(submission.model_dump(), sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM requests WHERE request_id = ?", (submission.request_id,)).fetchone()
            if existing:
                if existing["content_hash"] != fingerprint:
                    raise StateError("request_id_conflict")
                return False
            connection.execute(
                "INSERT INTO requests (request_id, content_hash, subject, message, status, created_at) VALUES (?, ?, ?, ?, 'analyzing', ?)",
                (submission.request_id, fingerprint, submission.subject, submission.message, datetime.now(timezone.utc).isoformat()),
            )
            return True

    def claim_analysis_retry(self, request_id):
        with self.transaction() as connection:
            row = self._row(connection, request_id)
            if row["status"] != "analysis_failed":
                raise StateError("analysis_not_retryable")
            connection.execute("UPDATE requests SET status = 'analyzing', error_code = NULL WHERE request_id = ?", (request_id,))
            return Submission(request_id=request_id, subject=row["subject"], message=row["message"])

    def finish_analysis(self, request_id, draft, metrics, error_code=None):
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE requests SET status = ?, draft = ?, metrics = ?, error_code = ? WHERE request_id = ? AND status = 'analyzing'",
                ("analysis_failed" if error_code else "pending_review", json.dumps(draft) if draft else None,
                 json.dumps(metrics), error_code, request_id),
            )
            if cursor.rowcount != 1:
                raise StateError("analysis_state_conflict")

    def decide(self, request_id, decision, repository):
        with self.transaction() as connection:
            row = self._row(connection, request_id)
            if decision == "reject":
                if row["status"] not in ("pending_review", "rejected"):
                    raise StateError("decision_conflict")
                connection.execute("UPDATE requests SET status = 'rejected' WHERE request_id = ?", (request_id,))
                return None
            if row["status"] in ("publishing", "published", "needs_reconciliation"):
                return None
            if row["status"] != "pending_review":
                raise StateError("decision_conflict")
            if not repository:
                raise StateError("repository_not_configured", 503)
            token = secrets.token_urlsafe(32)
            connection.execute(
                "UPDATE requests SET status = 'publishing', repository = ?, claim_token = ? WHERE request_id = ?",
                (repository, token, request_id),
            )
            return {"claim_token": token, "repository": repository, "draft": json.loads(row["draft"])}

    def finish_publication(self, request_id, token, issue_number=None):
        with self.transaction() as connection:
            row = self._row(connection, request_id)
            if not row["claim_token"] or not hmac.compare_digest(row["claim_token"].encode(), token.encode()):
                raise StateError("invalid_publication_claim", 403)
            if row["status"] == "published":
                if issue_number is not None and row["issue_number"] != issue_number:
                    raise StateError("publication_result_conflict")
                return
            if row["status"] not in ("publishing", "needs_reconciliation"):
                raise StateError("publication_state_conflict")
            connection.execute(
                "UPDATE requests SET status = ?, issue_number = ?, error_code = ? WHERE request_id = ?",
                ("published" if issue_number else "needs_reconciliation", issue_number,
                 None if issue_number else "publication_uncertain", request_id),
            )

    def reconcile(self, request_id, issue_number):
        with self.transaction() as connection:
            row = self._row(connection, request_id)
            if row["status"] == "published" and row["issue_number"] == issue_number:
                return
            if row["status"] not in ("publishing", "needs_reconciliation"):
                raise StateError("reconciliation_conflict")
            connection.execute(
                "UPDATE requests SET status = 'published', issue_number = ?, error_code = NULL WHERE request_id = ?",
                (issue_number, request_id),
            )


def public_record(row):
    return {
        "request_id": row["request_id"], "subject": row["subject"], "message": row["message"],
        "status": row["status"], "draft": json.loads(row["draft"]) if row["draft"] else None,
        "metrics": json.loads(row["metrics"]) if row["metrics"] else None,
        "error_code": row["error_code"], "created_at": row["created_at"],
        "issue_url": f"https://github.com/{row['repository']}/issues/{row['issue_number']}" if row["issue_number"] else None,
    }
