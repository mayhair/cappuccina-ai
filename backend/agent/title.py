from langchain_core.messages import HumanMessage, SystemMessage

from agent.model import make_model

TITLE_SYSTEM_PROMPT = (
    "You title chats. Given the first user message and the assistant's first reply, "
    "output a title of at most 6 words. Plain text only - no quotes, no trailing "
    "punctuation, same language as the user."
)

MAX_WORDS = 8
MAX_CHARS = 64


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") for part in content if isinstance(part, dict)
    )


def _clean(raw: str) -> str:
    title = " ".join((raw or "").split())
    title = title.strip("\"'`\u201c\u201d\u2018\u2019")
    title = title.rstrip(".,;:!?-\u2013\u2014")
    title = " ".join(title.split()[:MAX_WORDS])
    if len(title) > MAX_CHARS:
        title = title[:MAX_CHARS].rstrip()
    return title.strip()


async def generate_title(user_message: str, assistant_reply: str) -> str:
    try:
        model = make_model(reasoning="")
        result = await model.ainvoke(
            [
                SystemMessage(TITLE_SYSTEM_PROMPT),
                HumanMessage(
                    f"User: {user_message}\nAssistant: {assistant_reply[:500]}\nTitle:"
                ),
            ]
        )
        return _clean(_content_text(getattr(result, "content", "")))
    except Exception:
        return ""
