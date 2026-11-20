"""Tiny, dependency-free User-Agent summariser for the dashboard."""


def summarise(ua: str) -> str:
    if not ua:
        return "Unknown device"
    u = ua.lower()
    # Operating system / device
    if "iphone" in u:
        device = "iPhone"
    elif "ipad" in u:
        device = "iPad"
    elif "android" in u:
        device = "Android"
    elif "windows" in u:
        device = "Windows"
    elif "mac os" in u or "macintosh" in u:
        device = "Mac"
    elif "linux" in u:
        device = "Linux"
    else:
        device = "Device"
    # Browser (order matters: Edge/Chrome both contain "safari")
    if "edg/" in u or "edge" in u:
        browser = "Edge"
    elif "firefox" in u:
        browser = "Firefox"
    elif "crios" in u:
        browser = "Chrome"
    elif "chrome" in u:
        browser = "Chrome"
    elif "safari" in u:
        browser = "Safari"
    else:
        browser = "browser"
    return f"{device} · {browser}"
