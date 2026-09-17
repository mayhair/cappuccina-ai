from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    # Optional thinking trace sent back by the client so stored history keeps
    # the assistant's reasoning/tools/ordered parts across turns.
    reasoning: str = ""
    tools: list = []
    parts: list = []
    thinking_done: bool = Field(default=False, alias="thinkingDone")
    thought_seconds: float | None = Field(default=None, alias="thoughtSeconds")

    model_config = {"populate_by_name": True}


class ChatRequest(BaseModel):
    chat_id: str = Field(alias="chatId")
    messages: list[Message]
    reasoning: str = "medium"
    mode: str = "chat"
    subject: str | None = None
    generate_title: bool = Field(default=False, alias="generateTitle")

    model_config = {"populate_by_name": True}


class ToolCall(BaseModel):
    name: str
    display: str = ""
    args: dict
    result: str


class ChatResponse(BaseModel):
    role: str = "assistant"
    content: str
    reasoning: str = ""
    tool_calls: list[ToolCall] = []


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(alias="displayName")

    model_config = {"populate_by_name": True}


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str = Field(alias="displayName")
    avatar_url: str | None = Field(default=None, alias="avatarUrl")
    gmail_connected: bool = Field(default=False, alias="gmailConnected")

    model_config = {"populate_by_name": True}


class GmailStatusResponse(BaseModel):
    connected: bool
    scopes: list[str] = []


class UpdateDraftRequest(BaseModel):
    to: str
    subject: str = ""
    body: str


class CreateChatRequest(BaseModel):
    mode: str = "chat"
    subject: str | None = None


class RenameChatRequest(BaseModel):
    title: str
