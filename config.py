"""Configuration read from environment variables (optionally from backend/.env).

No secret is stored in the source code. SECRET_KEY must come from the environment (backend/.env).
If it is missing or still a placeholder, a random development key is generated ONCE and saved to
backend/.dev_secret_key (git-ignored), so sessions survive restarts but the key is never a value
that is public in the repository. In ENVIRONMENT=production the application refuses to start instead.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # the backend/ folder

MIN_SECRET_LENGTH = 32
_PLACEHOLDER_PREFIXES = ("change-me", "changeme", "dev-only", "your-secret", "secret", "replace-me")


def _load_env_file(path: Path) -> None:
    """Tiny .env reader (KEY=VALUE per line) so we need no extra dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be a whole number.") from None


def is_weak_secret(value: str) -> bool:
    """True for empty, short or obviously-placeholder secrets (e.g. the text copied from .env.example)."""
    v = (value or "").strip()
    return len(v) < MIN_SECRET_LENGTH or v.lower().startswith(_PLACEHOLDER_PREFIXES)


def _resolve_secret_key(environment: str) -> tuple:
    """Return (secret, source). source is 'env' for a good SECRET_KEY, otherwise 'generated'."""
    configured = os.environ.get("SECRET_KEY", "")
    if configured and not is_weak_secret(configured):
        return configured, "env"
    if environment == "production":
        raise RuntimeError(
            "SECRET_KEY is missing, too short (< %d characters) or still a placeholder. Set a long random value in "
            "backend/.env, for example: python -c \"import secrets; print(secrets.token_urlsafe(48))\"" % MIN_SECRET_LENGTH)
    key_file = BASE_DIR / ".dev_secret_key"
    try:
        if key_file.exists():
            stored = key_file.read_text(encoding="utf-8").strip()
            if not is_weak_secret(stored):
                return stored, "generated"
        generated = secrets.token_urlsafe(48)
        key_file.write_text(generated, encoding="utf-8")
        try:
            key_file.chmod(0o600)
        except OSError:  # e.g. Windows
            pass
        return generated, "generated"
    except OSError:  # read-only folder: fall back to a per-process key (sessions end on restart)
        return secrets.token_urlsafe(48), "generated"


class Settings:
    APP_NAME = "FoodBridge"
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").strip().lower()
    DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'foodbridge.db'}")
    SECRET_KEY, SECRET_KEY_SOURCE = _resolve_secret_key(ENVIRONMENT)
    ACCESS_TOKEN_EXPIRE_MINUTES = _int("ACCESS_TOKEN_EXPIRE_MINUTES", 480)
    CORS_ORIGINS = [o.strip() for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
    BCRYPT_ROUNDS = _int("BCRYPT_ROUNDS", 12)
    # Brute-force protection for POST /api/auth/login (per client IP + email, in memory).
    LOGIN_MAX_ATTEMPTS = _int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_WINDOW_SECONDS = _int("LOGIN_WINDOW_SECONDS", 900)
    # Optional road-distance enhancement. The app works fully with this off.
    USE_OSRM = _bool("USE_OSRM", False)
    OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
    # How often the background task marks overdue donations as EXPIRED (seconds).
    EXPIRY_CHECK_SECONDS = _int("EXPIRY_CHECK_SECONDS", 60)


settings = Settings()

if "*" in settings.CORS_ORIGINS and settings.ENVIRONMENT == "production":
    raise RuntimeError("CORS_ORIGINS must list explicit origins in production; '*' is not allowed.")
