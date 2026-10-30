from typing import Literal

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


CHAT_MODES = ("chat", "tutor", "story")
# Story worlds, matching src/lib/storyWorlds.js. "cappuccina" is her own.
STORY_WORLDS = (
    "cappuccina", "fantasy", "scifi", "mystery", "historical", "everyday", "fandom", "custom",
)


class StorySetup(BaseModel):
    """A story chat's world, opening and cast.

    ``cast`` holds lore-book character ids and ``characters`` the user's saved
    character ids. ``player_character`` is the saved character the user plays
    as, and ``player`` any notes on who they play. A setup without a world predates worlds: it is Cappuccina's
    world with her in the scene, which is what the defaults give.
    """

    world: Literal[STORY_WORLDS] = "cappuccina"  # pyrefly: ignore [invalid-literal]
    world_text: str = Field(default="", max_length=2000, alias="worldText")
    scenario: str = Field(default="", max_length=2000)
    with_cappuccina: bool = Field(default=True, alias="withCappuccina")
    cast: list[str] = Field(default=[], max_length=12)
    characters: list[int] = Field(default=[], max_length=12)
    player: str = Field(default="", max_length=1000)
    player_character: int | None = Field(default=None, alias="playerCharacter")

    model_config = {"populate_by_name": True}


class StoryCharacterRequest(BaseModel):
    name: str
    description: str = ""
    voice: str = ""


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
    story: StorySetup | None = None
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
    display_name: str | None = Field(default=None, alias="displayName")
    adult_mode: bool | None = Field(default=None, alias="adultMode")
    memory_enabled: bool | None = Field(default=None, alias="memoryEnabled")
    story_choices: bool | None = Field(default=None, alias="storyChoices")

    model_config = {"populate_by_name": True}


# ---------- email + password sign-in ----------
# ``lang`` is the interface language, so the emails match it (app/mailer.py).

class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str
    name: str = Field(default="", max_length=80)
    turnstile_token: str = Field(default="", alias="turnstileToken")
    lang: str = "en"

    model_config = {"populate_by_name": True}


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str


class EmailRequest(BaseModel):
    email: str = Field(max_length=254)
    lang: str = "en"


class TokenRequest(BaseModel):
    token: str = Field(max_length=200)


class ResetPasswordRequest(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(alias="newPassword")
    lang: str = "en"

    model_config = {"populate_by_name": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(default="", alias="currentPassword")
    new_password: str = Field(alias="newPassword")
    lang: str = "en"

    model_config = {"populate_by_name": True}


class AuthOptionsResponse(BaseModel):
    google: bool
    password: bool
    turnstile_site_key: str = Field(alias="turnstileSiteKey")

    model_config = {"populate_by_name": True}


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str = Field(alias="displayName")
    avatar_url: str | None = Field(default=None, alias="avatarUrl")
    has_password: bool = Field(default=False, alias="hasPassword")
    google_linked: bool = Field(default=False, alias="googleLinked")
    password_auth_enabled: bool = Field(default=True, alias="passwordAuthEnabled")
    gmail_connected: bool = Field(default=False, alias="gmailConnected")
    calendar_connected: bool = Field(default=False, alias="calendarConnected")
    gmail_enabled: bool = Field(default=False, alias="gmailEnabled")
    calendar_enabled: bool = Field(default=False, alias="calendarEnabled")
    adult_mode: bool = Field(default=False, alias="adultMode")
    adult_mode_enabled: bool = Field(default=True, alias="adultModeEnabled")
    memory_enabled: bool = Field(default=True, alias="memoryEnabled")
    story_choices: bool = Field(default=True, alias="storyChoices")
    story_mode_enabled: bool = Field(default=True, alias="storyModeEnabled")

    model_config = {"populate_by_name": True}


class MemoryRequest(BaseModel):
    content: str


class StoryChapterRequest(BaseModel):
    text: str


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
    story: StorySetup | None = None


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
    # The active commit the tab rendered. None is what a site with no commit
    # yet sends (an interrupted first run); the route then only matches when
    # the site's active_commit is still None.
    base_version_id: str | None = Field(default=None, alias="baseVersionId")

    model_config = {"populate_by_name": True}


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


class TtsJobResponse(BaseModel):
    id: str
