"""Interactive CLI to poke the Cappuccina backend.

Usage:
    .\\.venv\\Scripts\\python.exe chat_cli.py            # talks to http://localhost:8000
    .\\.venv\\Scripts\\python.exe chat_cli.py --url http://localhost:8001
    .\\.venv\\Scripts\\python.exe chat_cli.py --no-stream   # full JSON reply, one shot
    .\\.venv\\Scripts\\python.exe chat_cli.py --reasoning high --mode tutor --subject fractions

Commands inside the REPL: /mode, /reasoning, /subject, /showreasoning, /reset, /quit
"""

import argparse
import json
import sys

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8000")
parser.add_argument(
    "--reasoning",
    default="high",
    choices=["low", "high", "max"],
)
parser.add_argument("--mode", default="chat", choices=["chat", "tutor"])
parser.add_argument("--subject", default=None)
parser.add_argument(
    "--timezone",
    default="UTC",
    help="IANA timezone for the current date (e.g. Europe/Berlin)",
)
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
settings = {
    "reasoning": args.reasoning,
    "mode": args.mode,
    "subject": args.subject,
    "timezone": args.timezone,
    "showreasoning": "on",
}


def request_body():
    return {
        "chatId": "cli",
        "messages": history,
        "reasoning": settings["reasoning"],
        "mode": settings["mode"],
        "subject": settings["subject"],
        "timezone": settings["timezone"],
    }


def stream_reply(client):
    show_reasoning = settings["showreasoning"] not in {"off", "0", "no"}
    reply = ""
    thinking_open = False
    with client.stream("POST", "/api/v1/chat/stream", json=request_body()) as res:
        res.raise_for_status()
        event_name = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event_name = line[7:]
                continue
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            kind, event_name = event_name, None
            if kind == "token":
                chunk = payload.get("content", "")
                if thinking_open and chunk:
                    print()
                    thinking_open = False
                reply += chunk
                sys.stdout.write(chunk)
                sys.stdout.flush()
            elif kind == "reasoning":
                if not show_reasoning:
                    continue
                chunk = payload.get("content", "")
                if chunk:
                    if not thinking_open:
                        print("\n  [thinking] ", end="")
                        thinking_open = True
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            elif kind == "tool_call":
                if thinking_open:
                    print()
                    thinking_open = False
                print(f"\n  [tool] {payload.get('label', payload.get('name', ''))}")
            elif kind == "tool_result":
                if thinking_open:
                    print()
                    thinking_open = False
                content = payload.get("content", "")
                if len(content) > 400:
                    content = content[:400] + "..."
                status = "" if payload.get("ok", True) else " (failed)"
                print(f"  [tool result]{status} {content}")
        if thinking_open:
            print()
    return reply


def one_shot(client):
    res = client.post("/api/v1/chat", json=request_body())
    res.raise_for_status()
    body = res.json()
    if settings["showreasoning"] not in {"off", "0", "no"} and body.get("reasoning"):
        print(f"\n  [thinking] {body['reasoning']}")
    for call in body.get("tool_calls", []):
        args = ", ".join(f"{k}={v!r}" for k, v in call.get("args", {}).items())
        print(f"\n  [tool] {call.get('name', '')}({args})")
        result = call.get("result", "")
        if len(result) > 400:
            result = result[:400] + "..."
        print(f"  [tool result] {result}")
    reply = body["content"]
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
    f"reasoning={settings['reasoning']} mode={settings['mode']} subject={settings['subject']} showreasoning={settings['showreasoning']}"
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
