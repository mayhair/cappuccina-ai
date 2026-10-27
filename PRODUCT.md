# Product

<!-- impeccable:product-schema 1 -->

## Platform

web (installable progressive web app)

## Stack

React 19 and Vite for the frontend (`src/`). FastAPI, LangGraph and Python for the backend (`backend/`). PostgreSQL 18 for accounts, chats, memories and webpage records. Rootless Podman or Docker for the webpage sandboxes. Cloudflare Worker and Cloudflare Tunnel for the hosted deployment.

The chat model, image model and voice provider are set through environment variables. The hosted deployment uses DeepSeek V4.1 Flash through OpenRouter for chat and OpenAI for images.

## Users

Primary users are teenagers aged 13 and older and young adults. They use Cappuccina at home or while studying for open conversation, homework and tutoring, and small creative projects. They already know modern chat apps and expect the same grammar. They respond to a friendly character more than to a sterile tool.

Adults aged 18 and over can opt into Adult mode from Settings.

The app is live at `cappuccina.mayhair.net` and is run by one operator. Sign-in is open to any Google account.

## Product Purpose

Ballerina Cappuccina is a general-purpose AI chatbot with a fixed friendly personality, shown as Ballerina Cappuccina, a cup-headed ballerina in a pink tutu.

Open conversation is the default. Tutor mode turns the same chat into step-by-step teaching when the user asks for help learning. Tools extend the chat with web search, image generation and editing, file reading, long-term memory, free-media lookup, and a real webpage workspace that builds and previews sites inside the chat.

Success means a chat that feels warm and useful to a young user, stays honest about being an AI, and lets the user leave with something real, such as a finished drawing or a working webpage.

## Positioning

A character-embodied companion and tutor that can also build things. Generic chatbots can chat and answer questions. Cappuccina pairs a specific visual identity and voice with a per-chat website workspace, where she writes files, runs commands, previews the page, and keeps version history. The character makes the AI approachable for young users, and the app stays clearly identifiable as an AI.

## Operating Context

Users open Cappuccina in a laptop or mobile browser. They can install it as a progressive web app from the browser's install option.

Sign-in is Google-only. Each session lasts up to 30 days through one cookie.

Sessions are casual. A user can switch between hanging out, getting homework help, and building a webpage in the same day. Each chat keeps its own conversation and, if used, its own webpage project.

The interface supports light, dark and system themes. English, Urdu and Roman Urdu are available, with right-to-left layout for Urdu.

The app shows an early-alpha notice and a 13+ age gate.

## Capabilities and Constraints

Live features:

- Google sign-in, a 30-day session cookie, and account deletion that removes the account, chats, files, images, webpages, memories and Google connections.
- Streaming chat with Low, High and Max reasoning effort.
- Chat and Tutor modes. Tutor mode teaches step by step and has no subject picker.
- Message editing, regeneration and branch history, so a user can move between versions of a conversation.
- Attachments: images for vision, documents (PDF, DOCX, PPTX, XLSX, plain text and code) parsed to text, and audio, video and font assets for webpage use.
- Image generation and editing. Optional character reference images from the lore book keep a character's look consistent.
- Long-term memory. The agent can save, update and search facts, and the user can add, edit, delete or disable memories in Settings. Caps are 200 memories per user and 300 characters each. A privacy filter applies while Adult mode is off.
- Web search and webpage reading.
- FMHY free-media lookup for "where can I watch or read this" questions. NSFW-tagged results are filtered out.
- A webpage canvas per chat. The site is a real folder with a container terminal, `npm` builds, live preview, code editing, version history, and a download as a `.zip`. A headless Chromium check tests the page before Cappuccina calls it ready.
- Voice input through the browser's speech recognition. The server receives only the text.
- Adult mode for users 18 and older. The browser checks the entered birthday, and the server stores only an on or off flag. The mode loosens the young-user safety boundaries. An operator can remove the option with `ADULT_MODE_ENABLED`.
- Rate limiting of paid chat requests to 10 per user per minute by default.
- No ads, tracking scripts or analytics. Data lives in PostgreSQL and on disk, and backups follow a 30-day retention limit.

Currently off or limited:

- Gmail and Google Calendar integrations remain in the code but are off by default and are turned off for users in the hosted deployment. They stay off because Google classifies the Gmail scopes as restricted and the Calendar scope as sensitive. Requesting them adds a heavy verification burden, including a CASA security assessment for Gmail. Enabling them needs operator configuration in `backend/.env` and Google Cloud. When on, Gmail tools can search and read mail and save drafts but never send. Calendar tools can add and edit events, and a deletion needs user confirmation. Turning a service off deletes and revokes the stored tokens.
- Voice replies are off in code. The Fish Audio terms allow training on submitted text, which conflicts with Google rules for Gmail and Calendar data, so the text-to-speech endpoint stays disabled until a provider that does not train on user text replaces it.
- The free-media tools can be disabled by leaving `FMHY_MCP_URL` empty.
- Webpage containers run generated code without database access and stop after 10 idle minutes by default.

Product constraints:

- The app must stay clearly identifiable as an AI chatbot. The default audience is minors, and Adult mode is an explicit 18+ opt-in.
- Chat history is stored server-side. The browser keeps only preferences such as language, theme, reasoning effort and the open chat.
- Chat model providers in the hosted deployment use Zero Data Retention. The image provider keeps requests for a limited time for misuse checks, as described in the privacy policy.

## Brand Commitments

- Character: Ballerina Cappuccina, a cup-headed ballerina in a pink tutu with big amber eyes. The reference images in `backend/agent/lore/characters/refs/` are the canonical look.
- The wider cast comes from Italian Brainrot internet folklore (Tralalero Tralala, Espressona Signora, Tung Tung Tung Sahur, Bombardiro Crocodilo, Chimpanzini Bananini, Ta Ta Ta Sahur and Ballerino Lololo). These characters are public-domain internet folklore, not app-owned properties. The lore book defines their voices and behavior.
- Voice: warm, playful and reassuring, with light café and Italian touches. The app always states that Cappuccina is an AI and tells users to double-check important information.
- Friendly rounded display type for headings.
- American English is the default for UI copy, code, comments, docs and prompts. Other languages live in `src/i18n`.

## Evidence on Hand

- Character reference images and lore entries in `backend/agent/lore/` and `backend/agent/lore/characters/refs/`.
- Product art, icons, manifest and offline page in `assets/`.
- Terms of Service and Privacy Policy in `legal/`.
- A running deployment at `cappuccina.mayhair.net`.
- No testimonials, benchmarks, prices or third-party case studies exist. Future work must not invent them.

## Product Principles

1. Familiar first: keep the chat grammar that users already know, with no relearning.
2. Presence, not costume: the character is felt in the writing and the interaction, not pasted on.
3. Honest about being an AI, warm while saying it.
4. Tutoring is a mode, not a different product. Motivation is invited, never forced.
5. Age-clear language: simple, kind and never condescending.

## Accessibility & Inclusion

The audience includes younger readers and speakers of Urdu and Roman Urdu. Use clear type sizes, plain language, obvious affordances and visible states. Meet the standard web baseline for contrast, focus visibility, keyboard use and right-to-left layout.

## Language

Default English is American English: spelling in UI copy, code, comments, docs and LLM prompts (for example "color", "center", "behavior", "canceled", "license"). Proper nouns and quoted third-party text keep their original spelling. Other locales go in their own `src/i18n` file rather than by editing the default.
