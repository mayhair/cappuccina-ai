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

# PostgreSQL connection string (libpq URL). The default matches compose.yaml.
# An empty host connects over the local Unix socket, e.g. postgresql:///cappuccina.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://cappuccina:cappuccina@127.0.0.1:5432/cappuccina"
)

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

# Adult mode. When on, eligible users can opt into 18+ handling from Settings
# (their birthday is checked in the browser and only a boolean is stored). Set
# to 0/false/no/off to remove the option entirely: the Settings section, the
# PATCH endpoint, and the chat prompt all ignore it.
ADULT_MODE_ENABLED = os.getenv("ADULT_MODE_ENABLED", "1").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
    "off",
)

# Character reference images. When on, the image tools attach a character's
# official look from lore/characters/refs/ whenever the model names them in the
# `characters` argument, routing the call through the edit endpoint. Set to
# 0/false/no/off to always draw from text alone.
CHARACTER_REF_IMAGES = os.getenv("CHARACTER_REF_IMAGES", "1").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
    "off",
)

# ---------- Text-to-speech (Fish Audio) ----------
# Reads assistant replies aloud. POST https://api.fish.audio/v1/tts is proxied
# behind /api/v1/tts (see app/tts.py). Leave FISH_API_KEY empty to disable TTS
# (the endpoint answers 503 and the frontend silently skips speaking).
# TTS_ENABLED switches it off even with a key: voice replies are paused because
# Fish Audio may train on the text it receives, which can include Gmail and
# Calendar details. The frontend hides the switch too (VOICE_REPLIES_AVAILABLE
# in src/App.jsx).
TTS_ENABLED = False
FISH_API_KEY = os.getenv("FISH_API_KEY", "")
FISH_TTS_REFERENCE_ID = os.getenv(
    "FISH_TTS_REFERENCE_ID", "e9f4485fdde24821835dfe129d3c22b0"
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

# Gmail and Google Calendar integrations. Both are off by default (they saw
# little use). Set to 1/true/yes/on to bring one back: the agent tools, the
# connect/draft/event routes and the Settings → Connections forms all follow
# these. At startup, every user's grant for a switched-off service is deleted
# (and revoked at Google; see google_oauth.purge_grants), so users have to
# connect again once it's back on.
GMAIL_ENABLED = os.getenv("GMAIL_ENABLED", "0").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
    "off",
)
CALENDAR_ENABLED = os.getenv("CALENDAR_ENABLED", "0").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
    "off",
)

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

# ---------- Containerized website workspaces ----------
# Each chat's webpage lives in its own rootless container (see app/sitebox.py).
# `docker` behaves identically to `podman`; every invocation is [CLI, ...].
SITE_CONTAINER_CLI = os.getenv("SITE_CONTAINER_CLI", "podman")
# Containers idle longer than this get stopped by the background sweeper and
# started again on the next command, so an abandoned chat costs nothing.
SITE_IDLE_MINUTES = int(os.getenv("SITE_IDLE_MINUTES", "10"))
# Default shell timeout for webpage_shell; the tool clamps each call to 1-600.
SITE_EXEC_TIMEOUT = int(os.getenv("SITE_EXEC_TIMEOUT", "120"))
# `npm run build` gets a longer leash than a plain shell command: a cold
# install plus a framework build routinely takes minutes.
SITE_BUILD_TIMEOUT = int(os.getenv("SITE_BUILD_TIMEOUT", "300"))

# ---------- Rate limiting ----------
# Requests per user per minute to the endpoints that call paid models
# (chat, chat stream, text-to-speech). Everything else is unlimited.
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# ---------- Memory ----------
# Long-term facts about the user that the agent saves through its memory tools
# and the user manages from Settings. The caps apply to everyone; the content
# filters in agent/memory.py apply only while adult mode is off.
MEMORY_MAX_CHARS = 300
MEMORY_MAX_PER_USER = 200
# How many of the newest memories ride along in every prompt; the rest are
# reachable through the search_memories tool.
MEMORY_INJECT_LIMIT = 25

REASONING_LEVELS = {"off", "low", "high", "max"}

TUTOR_SUFFIX = """
You are now in TUTOR MODE{for_subject}. Don't just give answers — teach. \
One step at a time, one question at a time, and check understanding before moving on. \
Always cheer progress on."""

# Unused - there used to be a subject field, but I removed it for now.
TUTOR_FOR_SUBJECT = " for {subject}"
