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
