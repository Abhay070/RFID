"""GHOST ID configuration, read from environment variables (or a .env file)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.base_url = os.getenv("GHOST_BASE_URL", "http://localhost:8000").rstrip("/")
        self.db_path = os.getenv("GHOST_DB", str(ROOT / "data" / "ghost.db"))
        self.profile_path = os.getenv("GHOST_PROFILE", str(ROOT / "data" / "profile.json"))
        default_secure = "1" if self.base_url.startswith("https://") else "0"
        self.secure_cookies = os.getenv("GHOST_SECURE_COOKIES", default_secure) == "1"
        self.trust_proxy = os.getenv("GHOST_TRUST_PROXY", "0") == "1"
        # Push notifications to your iPhone via the free ntfy app (https://ntfy.sh)
        self.ntfy_server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        self.ntfy_topic = os.getenv("NTFY_TOPIC", "")
        self.notify_taps = os.getenv("GHOST_NOTIFY_TAPS", "0") == "1"
        # How long a paired phone stays trusted, and how long a pairing link is valid.
        self.device_days = int(os.getenv("GHOST_DEVICE_DAYS", "365"))
        self.pair_minutes = int(os.getenv("GHOST_PAIR_MINUTES", "10"))
        # Visitor recon demo (fingerprint + consent camera/location). Off with GHOST_RECON=0.
        self.recon_enabled = os.getenv("GHOST_RECON", "1") == "1"
        # Coarse IP->city lookup on each visitor tap (needs outbound https).
        self.geo_enabled = os.getenv("GHOST_GEO", "1") == "1"


settings = Settings()
