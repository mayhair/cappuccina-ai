import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_ID = os.getenv("MODEL_ID", "z-ai/glm-5.3-flash")
# Optional smaller/cheaper model for chat titles; falls back to MODEL_ID.
TITLE_MODEL_ID = os.getenv("TITLE_MODEL_ID", "")
PARALLEL_API_KEY = os.getenv("PARALLEL_API_KEY", "")

# Image generation (generate_image agent tool). The model must return images
# as base64; both the returned bytes and their dimensions are persisted.
IMAGE_MODEL_ID = os.getenv("IMAGE_MODEL_ID", "gpt-image-2.5-flare")
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")

# ---------- Google OAuth ----------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# Key material used to encrypt refresh/access tokens at rest. Any string works;
# it is hashed into a Fernet key (see app/google_oauth.py).
TOKEN_ENC_KEY = os.getenv("TOKEN_ENC_KEY", "")
# Origin users actually browse (the Vite dev server through its /api proxy).
# Google redirect URIs and post-login redirects are built from this.
APP_ORIGIN = os.getenv("APP_ORIGIN", "http://localhost:5173").rstrip("/")

GOOGLE_LOGIN_SCOPES = ["openid", "email", "profile"]
GOOGLE_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]
# Read + write events on the user's calendars. We only ever touch the primary
# calendar, so this single scope is enough (no calendar.readonly needed).
GOOGLE_CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
]

REASONING_LEVELS = {"low", "high", "max"}

# The current date formatted like e.g. "15 September 2026"
now_utc = datetime.now(timezone.utc)
CURRENT_DATE = f"{now_utc.day} {now_utc.strftime('%B %Y')}"

TUTOR_SUFFIX = """
You are now in TUTOR MODE{for_subject}. Don't just give answers — teach. \
One step at a time, one question at a time, and check understanding before moving on. \
Always cheer progress on."""

# Unused - there used to be a subject field, but I removed it for now.
TUTOR_FOR_SUBJECT = " for {subject}"
