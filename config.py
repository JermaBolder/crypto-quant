"""Central runtime config — read from env, default to localhost.

12-factor: the SAME code runs on the host (localhost) or inside a container
(compose service names 'redis' / 'questdb') purely by setting env vars. No code
change between dev and prod — that's the whole point.
"""
import os

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
QUESTDB_HOST = os.environ.get("QUESTDB_HOST", "127.0.0.1")
QUESTDB_HTTP = f"http://{QUESTDB_HOST}:9000"

# Telegram alerts (watchdog). Empty = push disabled, watchdog logs only.
# Secrets live in the untracked .env (compose auto-loads it), never in git.
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
