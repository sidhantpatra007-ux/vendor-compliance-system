import os
from dotenv import load_dotenv

load_dotenv()

COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY")
COMPANIES_HOUSE_STREAM_API_KEY = os.getenv("COMPANIES_HOUSE_STREAM_API_KEY")
COMPANIES_HOUSE_STREAM_ENABLED = os.getenv(
    "COMPANIES_HOUSE_STREAM_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}
SANCTIONS_SERVICE_URL = os.getenv("SANCTIONS_SERVICE_URL", "http://sanctions-service:8001")
ALLOWED_ORIGINS = [v.strip() for v in os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
).split(",") if v.strip()]
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET")
DASHBOARD_COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
ALERT_SCORE_DROP_THRESHOLD = int(os.getenv("ALERT_SCORE_DROP_THRESHOLD", "-5"))
ALERT_SLA_HOURS = int(os.getenv("ALERT_SLA_HOURS", "24"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Postgres (Supabase) connection string — the full postgresql://... URI,
# password included, set in .env. Replaces the old DATABASE_PATH/SQLite
# construction entirely; no fallback to SQLite, since running two
# different database engines (dev on SQLite, prod on Postgres) risks
# subtle behavior differences going unnoticed until they bite in prod.
DATABASE_URL = os.getenv("DATABASE_URL")

if not COMPANIES_HOUSE_API_KEY:
    raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set. Check your .env file.")

if COMPANIES_HOUSE_STREAM_ENABLED and not COMPANIES_HOUSE_STREAM_API_KEY:
    raise RuntimeError(
        "COMPANIES_HOUSE_STREAM_API_KEY is required when "
        "COMPANIES_HOUSE_STREAM_ENABLED=true. Register a separate Streaming "
        "API application in the Companies House Developer Hub."
    )

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set. Check your .env file.")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

if ALERT_SLA_HOURS < 1 or AUDIT_RETENTION_DAYS < 1:
    raise RuntimeError("ALERT_SLA_HOURS and AUDIT_RETENTION_DAYS must be positive integers.")

if bool(DASHBOARD_PASSWORD) != bool(DASHBOARD_SESSION_SECRET):
    raise RuntimeError("Set both DASHBOARD_PASSWORD and DASHBOARD_SESSION_SECRET, or neither.")
