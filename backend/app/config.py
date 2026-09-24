import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL_ID = os.getenv("MODEL_ID", "z-ai/glm-5.3-flash")
# Optional smaller/cheaper model for chat titles; falls back to MODEL_ID.
TITLE_MODEL_ID = os.getenv("TITLE_MODEL_ID", "")
PARALLEL_API_KEY = os.getenv("PARALLEL_API_KEY", "")

# FMHY (Free Media Heck Yeah) free-media guide, reached through its MCP server.
# The agent tools fmhy_media_sites/fmhy_search/fmhy_get_links/fmhy_get_section/
# fmhy_list_sections
# proxy to this Streamable HTTP endpoint. Leave empty to disable the feature
# (the chat still runs, just without the free-media tools).
FMHY_MCP_URL = os.getenv("FMHY_MCP_URL", "https://fmhy-mcp.ken.tools/mcp")

# Image generation/editing (generate_image and image_edit agent tools). The
# model must return images as base64; both the returned bytes and their
# dimensions are persisted. Resolutions are chosen per call (1024x1024,
# 1536x1024, 1024x1536); there is no env-configured size.
IMAGE_MODEL_ID = os.getenv("IMAGE_MODEL_ID", "gpt-image-2.5-flare")

# ---------- Text-to-speech (Fish Audio) ----------
# Reads assistant replies aloud. POST https://api.fish.audio/v1/tts is proxied
# behind /api/v1/tts (see app/tts.py). Leave FISH_API_KEY empty to disable TTS
# (the endpoint answers 503 and the frontend silently skips speaking).
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_TTS_REFERENCE_ID = os.getenv(
    "FISH_TTS_REFERENCE_ID", "b676533aa3e6410db69373c197847582"
)
FISH_TTS_MODEL = os.getenv("FISH_TTS_MODEL", "s2.1-pro-free")

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

TUTOR_SUFFIX = """
You are now in TUTOR MODE{for_subject}. Don't just give answers — teach. \
One step at a time, one question at a time, and check understanding before moving on. \
Always cheer progress on."""

# Unused - there used to be a subject field, but I removed it for now.
TUTOR_FOR_SUBJECT = " for {subject}"
