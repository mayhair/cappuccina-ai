from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str
    # Tree identity: stable uid, parent link and sibling order. Optional so a
    # bare {role, content} history still syncs as a single linear branch.
    id: str | None = None
    parent_id: str | None = Field(default=None, alias="parentId")
    branch_index: int = Field(default=0, alias="branchIndex")
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
    # The visible path (root -> latest) the model should see.
    messages: list[Message]
    # Every node in the chat, including other branches, for persistence.
    tree: list[Message] = []
    # uid the streamed assistant reply is stored under.
    reply_id: str | None = Field(default=None, alias="replyId")
    reasoning: str = "high"
    mode: str = "chat"
    subject: str | None = None
    timezone: str | None = None
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
    calendar_connected: bool = Field(default=False, alias="calendarConnected")

    model_config = {"populate_by_name": True}


class GmailStatusResponse(BaseModel):
    connected: bool
    scopes: list[str] = []


class CalendarStatusResponse(BaseModel):
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


class SetActiveBranchRequest(BaseModel):
    leaf_id: str | None = Field(default=None, alias="leafId")

    model_config = {"populate_by_name": True}


class SetWebpageVersionRequest(BaseModel):
    version_id: str = Field(alias="versionId")

    model_config = {"populate_by_name": True}


class SaveWebpageFileRequest(BaseModel):
    path: str
    content: str
    base_version_id: str = Field(alias="baseVersionId")

    model_config = {"populate_by_name": True}


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


class TtsJobResponse(BaseModel):
    id: str
