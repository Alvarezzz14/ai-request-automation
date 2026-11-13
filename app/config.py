import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    api_token: str
    reviewer_token: str
    opencode_model: str = "opencode/glm-5.3-flash-free"
    github_repository: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/requests.sqlite3"),
            api_token=os.getenv("AUTOMATION_API_TOKEN", ""),
            reviewer_token=os.getenv("REVIEWER_TOKEN", ""),
            opencode_model=os.getenv("OPENCODE_MODEL", "opencode/glm-5.3-flash-free"),
            github_repository=os.getenv("GITHUB_REPOSITORY", ""),
        )
