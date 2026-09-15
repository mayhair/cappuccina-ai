from langchain_openai import ChatOpenAI
from app.config import MODEL_ID, OPENAI_API_KEY, REASONING_LEVELS


def make_model(reasoning: str = "medium", tools=None):
    if reasoning not in REASONING_LEVELS:
        reasoning = "medium"
    # gpt-5.6-luna rejects reasoning_effort+tools on /v1/chat/completions;
    # Responses API supports both.
    model = ChatOpenAI(
        model=MODEL_ID,
        api_key=OPENAI_API_KEY,
        reasoning_effort=reasoning,
        use_responses_api=True,
    )
    return model.bind_tools(tools) if tools else model
