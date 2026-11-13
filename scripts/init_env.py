"""Create local tokens without printing them or overwriting existing configuration."""
import secrets
from pathlib import Path

path = Path(__file__).resolve().parents[1] / ".env"
if path.exists():
    print(".env already exists; unchanged.")
else:
    path.write_text(
        f"AUTOMATION_API_TOKEN={secrets.token_urlsafe(32)}\n"
        f"REVIEWER_TOKEN={secrets.token_urlsafe(32)}\n"
        "OPENCODE_MODEL=opencode/glm-5.3-flash-free\n"
        "GITHUB_REPOSITORY=\n"
        f"N8N_ENCRYPTION_KEY={secrets.token_hex(32)}\n"
    )
    path.chmod(0o600)
    print("Local .env created. Tokens were not printed.")
