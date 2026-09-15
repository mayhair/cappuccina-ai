from app.config import CHAT_SYSTEM_PROMPT, TUTOR_SUFFIX


def build_system_prompt(mode: str, subject: str | None) -> str:
    prompt = CHAT_SYSTEM_PROMPT
    if mode == "tutor":
        prompt += TUTOR_SUFFIX.format(subject=subject or "")
    return prompt
