from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


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


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str = Field(alias="displayName")

    model_config = {"populate_by_name": True}


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str = Field(alias="displayName")

    model_config = {"populate_by_name": True}


class CreateChatRequest(BaseModel):
    mode: str = "chat"
    subject: str | None = None


class RenameChatRequest(BaseModel):
    title: str
