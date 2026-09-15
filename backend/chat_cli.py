"""Interactive CLI to poke the Cappuccina backend.

Usage:
    .\\.venv\\Scripts\\python.exe chat_cli.py            # talks to http://localhost:8000
    .\\.venv\\Scripts\\python.exe chat_cli.py --url http://localhost:8001
    .\\.venv\\Scripts\\python.exe chat_cli.py --no-stream   # full JSON reply, one shot
    .\\.venv\\Scripts\\python.exe chat_cli.py --reasoning high --mode tutor --subject fractions

Commands inside the REPL: /mode, /reasoning, /subject, /reset, /quit
"""

import argparse
import json
import sys

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8000")
parser.add_argument(
    "--reasoning",
    default="medium",
    choices=["none", "low", "medium", "high", "xhigh", "max"],
)
parser.add_argument("--mode", default="chat", choices=["chat", "tutor"])
parser.add_argument("--subject", default=None)
parser.add_argument(
    "--no-stream", action="store_true", help="use POST /chat instead of SSE"
)
args = parser.parse_args()

# Windows consoles default to cp1252 and choke on the emoji Cappuccina emits.
# pyrefly: ignore [missing-attribute]
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# pyrefly: ignore [missing-attribute]
sys.stdin.reconfigure(encoding="utf-8", errors="replace")

history = []
settings = {"reasoning": args.reasoning, "mode": args.mode, "subject": args.subject}


def request_body():
    return {
        "chatId": "cli",
        "messages": history,
        "reasoning": settings["reasoning"],
        "mode": settings["mode"],
        "subject": settings["subject"],
    }


def stream_reply(client):
    reply = ""
    with client.stream("POST", "/api/v1/chat/stream", json=request_body()) as res:
        res.raise_for_status()
        for line in res.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "{}":
                continue
            chunk = json.loads(payload).get("content", "")
            reply += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()
    return reply


def one_shot(client):
    res = client.post("/api/v1/chat", json=request_body())
    res.raise_for_status()
    reply = res.json()["content"]
    print(reply)
    return reply


def handle_command(text):
    cmd, _, value = text[1:].partition(" ")
    value = value.strip() or None
    if cmd == "quit":
        sys.exit(0)
    if cmd == "reset":
        history.clear()
        print("(history cleared)")
    elif cmd in settings:
        settings[cmd] = value
        print(f"({cmd} = {value})")
    else:
        print(f"(unknown command: /{cmd})")
    return cmd in {"quit", "reset", *settings}


print(f"Cappuccina CLI -> {args.url}  [stream={not args.no_stream}]")
print(
    f"reasoning={settings['reasoning']} mode={settings['mode']} subject={settings['subject']}"
)
print("Type a message, /help for commands, /quit to exit.\n")

with httpx.Client(base_url=args.url, timeout=httpx.Timeout(180, connect=10)) as client:
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.startswith("/"):
            handle_command(text)
            continue

        history.append({"role": "user", "content": text})
        print("cappuccina> ", end="")
        try:
            reply = one_shot(client) if args.no_stream else stream_reply(client)
            print()
        except httpx.HTTPStatusError as err:
            history.pop()
            print(f"\n[backend {err.response.status_code}] {err.response.text[:500]}")
            continue
        except httpx.HTTPError as err:
            history.pop()
            print(f"\n[connection error] {err}")
            continue
        history.append({"role": "assistant", "content": reply})
        print()
