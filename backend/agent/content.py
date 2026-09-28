"""Flatten LangChain message content to plain text.

Message content is either a string or a list of content blocks (usually dicts
with a ``text`` key). History serialization, tool-result capture, title
generation and FMHY rendering all want the text out of that union, so the walk
lives here once instead of in each consumer.
"""


def flatten_content(content) -> str:
    """Plain text from a LangChain content value (string or content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content)
