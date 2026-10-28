from langchain_openai import ChatOpenAI

from app.config import (
    MODEL_ID,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    REASONING_LEVELS,
    TITLE_MODEL_ID,
)

DEFAULT_REASONING = "high"

# OpenRouter returns the reasoning trace on the message/choice as a `reasoning`
# (or `reasoning_content`) string, and `reasoning_details` as structured
# blocks. langchain-openai intentionally does not extract these fields, so we
# subclass and copy them onto the LangChain message's additional_kwargs.
# Verified live against z-ai/glm-5.3-flash (provider StreamLake): the response
# message carries `reasoning`, and `_convert_dict_to_message` drops it.
_REASONING_KEYS = ("reasoning", "reasoning_content", "reasoning_details")


def _carry_reasoning(target, source: dict) -> None:
    for key in _REASONING_KEYS:
        value = source.get(key)
        if value:
            target.additional_kwargs[key] = value


class OpenRouterChatModel(ChatOpenAI):
    """ChatOpenAI that preserves OpenRouter's reasoning fields."""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(warnings=False)
        )
        for generation, choice in zip(result.generations, response_dict.get("choices", [])):
            message = getattr(generation, "message", None)
            raw = choice.get("message") or {}
            if message is not None and isinstance(raw, dict):
                _carry_reasoning(message, raw)
        return result

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta") or {}
        if isinstance(delta, dict):
            _carry_reasoning(generation_chunk.message, delta)
        return generation_chunk


def make_model(reasoning: str = DEFAULT_REASONING, tools=None, model_id: str = ""):
    # `reasoning=""` omits the reasoning field entirely. "none" explicitly
    # asks for zero thinking; models that don't support that effort level
    # (e.g. GLM) reject the request, so callers wanting cheap reasoning fall
    # back to "low" (see agent/title.py).
    if reasoning != "none" and reasoning and reasoning not in REASONING_LEVELS:
        reasoning = DEFAULT_REASONING
        
    extra_body = {}
    
    # The model runs on OpenRouter, which speaks the OpenAI Chat Completions
    # API (not the Responses API). Reasoning is requested through OpenRouter's
    # unified `reasoning` field, passed verbatim via extra_body so the OpenAI
    # SDK doesn't have to know about it.
    # "off" is the user-facing switch: it disables reasoning outright via
    # `enabled: false`, which OpenRouter maps per provider (unlike
    # effort="none", which some models reject).
    if reasoning == "off":
        extra_body["reasoning"] = {"enabled": False}
    elif reasoning:
        extra_body["reasoning"] = {"effort": reasoning}

    # Provider routing. `zdr` and `data_collection: deny` restrict routing to
    # providers that neither keep nor train on prompts, which the Privacy
    # Policy promises; they must stay on for every request.
    extra_body["provider"] = {
        "zdr": True,
        "data_collection": "deny",
        "preferred_min_throughput": {"p75": 35},
        "allow_fallbacks": True,
    }

    model = OpenRouterChatModel(
        model=model_id or MODEL_ID,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        extra_body=extra_body,
    )
    return model.bind_tools(tools) if tools else model
