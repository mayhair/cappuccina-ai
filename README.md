<p align="center">
  <img src="Cappuccina.jpg" alt="Ballerina Cappuccina, a cup-headed ballerina in a pink tutu" width="280">
</p>

<h1 align="center">Cappuccina AI</h1>

<p align="center">
  A friendly AI chatbot with a personality, a tutor mode, and a built-in webpage workshop.<br>
  <a href="https://cappuccina.mayhair.net">Try it live</a> · <a href="SETUP.md">Run it yourself</a>
</p>

---

## What is this?

Cappuccina AI is a general-purpose chatbot that talks to you as **Ballerina Cappuccina**, a cup-headed ballerina in a pink tutu. She's warm, playful, and a little bit Italian, and she's always upfront about being an AI.

You can just hang out and chat with her, ask for help with homework, or build something together. She can draw pictures, look things up on the web, remember things about you (if you let her), and even build a real working webpage right inside the chat.

It's made with teenagers (13+) and young adults in mind, so the tone stays kind and simple without talking down to anyone.

> **Heads up:** Cappuccina is in early alpha. Things may change or break, and like any AI she can get things wrong. Double-check anything important.

## What can she do?

- **Chat.** Normal, streaming conversation, like the chat apps you already know. Edit a message, regenerate a reply, and flip between different versions of the conversation.
- **Tutor mode.** Turns the same chat into step-by-step teaching when you want to actually learn something, not just get the answer.
- **Make and edit images.** Describe a picture and she'll draw it, or upload one and ask her to change it.
- **Read your files.** Drop in images, PDFs, Word, PowerPoint, Excel, text, or code files.
- **Search the web.** She can look things up and read webpages for you.
- **Remember things.** She can save facts you share so she remembers them next time. You can see, edit, turn off, or delete any memory in Settings.
- **Build webpages.** Every chat can have its own website project. She writes the files, runs builds, tests the page in a real browser, and shows you a live preview. You can edit the code yourself, roll back to older versions, and download the whole thing as a `.zip`.
- **Find free media.** Ask "where can I watch or read this?" and she'll point you to free sources.
- **Meet her friends.** The wider cast of Italian Brainrot characters (Tralalero Tralala, Tung Tung Tung Sahur, Bombardiro Crocodilo, and more) live in her lore book.

A few more nice things:

- Light, dark, and system themes
- English, Italian, Urdu, and Roman Urdu (with right-to-left layout for Urdu)
- Voice input through your browser
- Installable as an app on your phone or computer
- Adjustable reasoning effort (Off, Low, High, Max)
- An optional Adult mode for users 18 and over

## Your privacy

- You sign in with your Google account. There are no separate passwords.
- No ads, no tracking scripts, no analytics.
- Deleting your account removes everything: chats, files, images, webpages, and memories.
- The full details are in the [Privacy Policy](legal/privacy-policy.md) and [Terms of Service](legal/terms-of-service.md).

---

## For developers

Want to run your own Cappuccina, poke at the code, or contribute? Welcome!

### How it fits together

| Part | Built with | Lives in |
|---|---|---|
| Frontend | React 19 + Vite | [`src/`](src) |
| Backend | FastAPI + LangGraph (Python) | [`backend/`](backend) |
| Database | PostgreSQL 18 | [`compose.yaml`](compose.yaml) for local dev |
| Webpage sandboxes | Rootless Podman (or Docker) | [`backend/app/sitebox.py`](backend/app/sitebox.py) |
| Hosting | Cloudflare Worker + Tunnel | [`worker/`](worker), [`wrangler.jsonc`](wrangler.jsonc) |

The chat model is reached through OpenRouter and can be swapped with an environment variable. Images come from OpenAI, and web search from Parallel.

### Getting started

Everything you need to install, configure, and run the project (locally or on a server) is in **[SETUP.md](SETUP.md)**.

The short version, once your prerequisites and `backend/.env` are in place:

```bash
npm install
npm run dev:all
```

Then open http://localhost:5173.

### Where to look

- **The character.** Her personality, voice, and safety boundaries are plain Markdown files in [`backend/agent/lore/`](backend/agent/lore). Each friend has a page in [`characters/`](backend/agent/lore/characters).
- **The agent.** The LangGraph setup is in [`backend/agent/graph.py`](backend/agent/graph.py), and all her tools (search, images, memory, webpages) are in [`backend/agent/tools.py`](backend/agent/tools.py).
- **The API.** Routes are in [`backend/app/main.py`](backend/app/main.py). The [backend README](backend/README.md) lists the endpoints.
- **The UI.** Start at [`src/App.jsx`](src/App.jsx). Translations are in [`src/i18n/`](src/i18n), so adding a language means adding one file there.
- **Product goals.** [PRODUCT.md](PRODUCT.md) explains who the app is for and the principles behind its design.

### Running the tests

From `backend/` with the virtual environment active:

```bash
python -m pytest tests -q
```

See [SETUP.md, section 7](SETUP.md#7-testing) for the full test setup, including the frontend Playwright checks.

## License

The code is licensed under the [Apache License 2.0](LICENSE).

The images are licensed under [CC BY 4.0](LICENSE-ASSETS.md). Neither license covers the Cappuccina AI name and logo. The Italian Brainrot characters are internet folklore, and this project doesn't own them.

---

<p align="center">Made with ☕ and a little bit of ballet.</p>
