"""Connect apps: fire outbound webhooks when band events happen.

This is the automation engine. When a visitor taps, or a camera still or GPS fix
is captured, GHOST ID POSTs a short message to every enabled webhook subscribed
to that event. It supports Discord, Slack, ntfy, Home Assistant and any generic
JSON webhook (Zapier / IFTTT / n8n / Make). Everything runs in a background
thread and never raises, so a slow or broken webhook can't affect a tap.

Safety: outbound notifications only. GHOST ID never runs commands on anyone's
machine; an automation can only send a message to a URL the owner configured.
"""
import json
import threading
import time
import urllib.request

from . import db

EVENTS = ("tap", "photo", "location", "pair")
KINDS = ("discord", "slack", "ntfy", "generic", "home_assistant")


def _payload(kind: str, title: str, message: str, data: dict) -> bytes:
    if kind == "discord":
        return json.dumps({"content": f"**{title}**\n{message}"}).encode()
    if kind == "slack":
        return json.dumps({"text": f"*{title}*\n{message}"}).encode()
    if kind == "ntfy":
        return message.encode()  # ntfy reads the body as the message
    if kind == "home_assistant":
        return json.dumps({"title": title, "message": message, "data": data}).encode()
    return json.dumps({"event": data.get("event"), "title": title,
                       "message": message, "data": data}).encode()


def _send(hook_id: int, kind: str, url: str, title: str, message: str, data: dict) -> None:
    status = "ok"
    try:
        body = _payload(kind, title, message, data)
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"User-Agent": "ghost-id"})
        if kind == "ntfy":
            req.add_header("Title", title.encode("ascii", "ignore").decode())
        else:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            status = f"HTTP {r.status}"
    except Exception as exc:
        status = f"error: {exc}"[:120]
    try:
        with db.tx() as c:
            c.execute("UPDATE webhooks SET last_fired = ?, last_status = ? WHERE id = ?",
                      (time.time(), status, hook_id))
    except Exception:
        pass


def fire(event: str, title: str, message: str, data: dict | None = None) -> None:
    """Fire every enabled webhook subscribed to `event`. Non-blocking."""
    data = {**(data or {}), "event": event}
    try:
        with db.tx() as c:
            hooks = c.execute("SELECT * FROM webhooks WHERE enabled = 1").fetchall()
    except Exception:
        return
    for h in hooks:
        if event in (h["events"] or "").split(","):
            threading.Thread(target=_send,
                             args=(h["id"], h["kind"], h["url"], title, message, data),
                             daemon=True).start()
