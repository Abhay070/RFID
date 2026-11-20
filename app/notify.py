"""Push notifications to the owner's iPhone through ntfy (free app, no account needed)."""
import threading
import urllib.request

from .config import settings


def _send(title: str, message: str, click: str | None, priority: str, tags: str) -> None:
    req = urllib.request.Request(
        f"{settings.ntfy_server}/{settings.ntfy_topic}",
        data=message.encode("utf-8"),
        method="POST",
    )
    req.add_header("Title", title.encode("ascii", "ignore").decode())
    req.add_header("Priority", priority)
    req.add_header("Tags", tags)
    if click:
        req.add_header("Click", click)
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # never let a failed push break the app
        print(f"[ghost-id] ntfy push failed: {exc}")


def push(title: str, message: str, click: str | None = None,
         priority: str = "default", tags: str = "ghost") -> None:
    if not settings.ntfy_topic:
        return
    threading.Thread(target=_send, args=(title, message, click, priority, tags), daemon=True).start()
