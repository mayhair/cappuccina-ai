from langchain_core.messages import HumanMessage, SystemMessage

from app.config import TITLE_MODEL_ID

from agent.model import make_model

TITLE_SYSTEM_PROMPT = (
    "You title chats. Given the first user message and the assistant's first reply, "
    "output a short title of at most 4 words. Aim for 2-4 words. Plain text only - "
    "no quotes, no trailing periods or commas; a trailing ? or ! is fine. Same "
    "language as the user."
)

MAX_WORDS = 8
MAX_CHARS = 64


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def _clean(raw: str) -> str:
    title = " ".join((raw or "").split())
    title = title.strip("\"'`\u201c\u201d\u2018\u2019")
    title = title.rstrip(".,;:-\u2013\u2014")
    title = " ".join(title.split()[:MAX_WORDS])
    if len(title) > MAX_CHARS:
        title = title[:MAX_CHARS].rstrip()
    return title.strip()


# Try zero reasoning first; models that don't support effort="none" (e.g.
# GLM) reject the request, so retry with "low". Once a rejection is seen we
# skip "none" for the rest of the process's lifetime.
_REASONING_ATTEMPTS = ("none", "low")
_none_rejected = False


def _reasoning_attempts():
    return ("low",) if _none_rejected else _REASONING_ATTEMPTS


async def generate_title(user_message: str, assistant_reply: str) -> str:
    global _none_rejected
    messages = [
        SystemMessage(TITLE_SYSTEM_PROMPT),
        HumanMessage(
            f"User: {user_message}\nAssistant: {assistant_reply[:500]}\nTitle:"
        ),
    ]
    for reasoning in _reasoning_attempts():
        try:
            model = make_model(reasoning=reasoning, model_id=TITLE_MODEL_ID)
            result = await model.ainvoke(messages)
            return _clean(_content_text(getattr(result, "content", "")))
        except Exception:
            if reasoning == "none":
                _none_rejected = True
    return ""
