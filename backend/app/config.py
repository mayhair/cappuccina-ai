import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
MODEL_ID = os.getenv("MODEL_ID", "gpt-5.6-luna")

REASONING_LEVELS = {"none", "low", "medium", "high", "xhigh", "max"}

# The current date formatted like e.g. "15 September 2026"
now_utc = datetime.now(timezone.utc)
CURRENT_DATE = f"{now_utc.day} {now_utc.strftime('%B %Y')}"

TUTOR_SUFFIX = """
You are now in TUTOR MODE{for_subject}. Don't just give answers — teach. \
One step at a time, one question at a time, and check understanding before moving on. \
Always cheer progress on."""

# Unused - there used to be a subject field, but I removed it for now.
TUTOR_FOR_SUBJECT = " for {subject}"
