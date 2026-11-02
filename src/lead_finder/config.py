"""Settings loaded from .env locally, or from real environment variables on GitHub Actions.

Fails fast with one clear message listing every missing value, instead of crashing mid-run.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    google_api_key: str
    sheet_id: str
    service_account_info: dict | None
    max_requests_per_run: int
    contact_email: str
    scoring_file: Path


def _service_account_info(missing: list[str]) -> dict | None:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ConfigError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}") from e

    path_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not path_value:
        missing.append("GOOGLE_SERVICE_ACCOUNT_FILE (or GOOGLE_SERVICE_ACCOUNT_JSON)")
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.is_file():
        missing.append(f"GOOGLE_SERVICE_ACCOUNT_FILE (file not found: {path})")
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_settings(need_sheet: bool = True) -> Settings:
    """Read settings. `need_sheet=False` is for commands that only call Google Maps."""
    load_dotenv(PROJECT_ROOT / ".env")
    missing: list[str] = []

    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        missing.append("GOOGLE_API_KEY")

    sheet_id = os.getenv("SHEET_ID", "").strip()
    service_account = None
    if need_sheet:
        if not sheet_id:
            missing.append("SHEET_ID")
        service_account = _service_account_info(missing)

    if missing:
        raise ConfigError(
            "Missing settings in .env:\n  - " + "\n  - ".join(missing)
            + "\nCopy .env.example to .env and fill them in."
        )

    try:
        max_requests = int(os.getenv("MAX_REQUESTS_PER_RUN", "30"))
    except ValueError as e:
        raise ConfigError("MAX_REQUESTS_PER_RUN must be a whole number") from e

    return Settings(
        google_api_key=api_key,
        sheet_id=sheet_id,
        service_account_info=service_account,
        max_requests_per_run=max_requests,
        contact_email=os.getenv("CONTACT_EMAIL", "").strip(),
        scoring_file=PROJECT_ROOT / "scoring.toml",
    )
