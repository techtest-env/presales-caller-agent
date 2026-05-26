"""
Relai Caller Agent — Diagnostic Script
Checks: asyncio policy, Sarvam API reachability, DB connection, duplicate records.
"""
import sys
import os
import asyncio
import time

os.environ['SSL_CERT_FILE'] = __import__('certifi').where()
from dotenv import load_dotenv
load_dotenv(".env")

print("=" * 60)
print("RELAI AGENT DIAGNOSTICS")
print("=" * 60)

# ── 1. asyncio event loop policy ─────────────────────────────────
print("\n[1] asyncio event loop policy")
policy = asyncio.get_event_loop_policy()
policy_name = type(policy).__name__
print(f"    Current policy : {policy_name}")
if sys.platform == "win32":
    if isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy):
        print("    Status         : OK — WindowsSelectorEventLoopPolicy is active")
    else:
        print("    Status         : WARNING — ProactorEventLoopPolicy is active (can cause WebSocket issues)")
        print("    Fix needed     : set WindowsSelectorEventLoopPolicy at top of agent.py")
else:
    print("    Status         : OK (non-Windows platform)")

# ── 2. Sarvam API reachability ────────────────────────────────────
print("\n[2] Sarvam API reachability")
import urllib.request
import urllib.error
import ssl

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")

def ping_https(url, label, timeout=10):
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"api-subscription-key": SARVAM_API_KEY})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            elapsed = (time.time() - t0) * 1000
            print(f"    {label}: HTTP {resp.status} — {elapsed:.0f}ms")
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - t0) * 1000
        # 4xx means server is reachable, just auth/method issue
        print(f"    {label}: HTTP {e.code} (reachable) — {elapsed:.0f}ms")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"    {label}: UNREACHABLE — {e} ({elapsed:.0f}ms)")

ping_https("https://api.sarvam.ai/text-to-speech", "TTS REST endpoint")
ping_https("https://api.sarvam.ai/speech-to-text",  "STT REST endpoint")

# WebSocket reachability check
async def ping_ws(url, label, timeout=10):
    t0 = time.time()
    try:
        import websockets
        async with websockets.connect(
            url,
            extra_headers={"api-subscription-key": SARVAM_API_KEY},
            open_timeout=timeout
        ) as ws:
            elapsed = (time.time() - t0) * 1000
            print(f"    {label}: WebSocket connected — {elapsed:.0f}ms")
            await ws.close()
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"    {label}: {type(e).__name__} — {e!s:.80} ({elapsed:.0f}ms)")

try:
    asyncio.run(ping_ws("wss://api.sarvam.ai/text-to-speech-websocket", "TTS WebSocket"))
except Exception as e:
    print(f"    TTS WebSocket: could not run check — {e}")

# ── 3. Database connection & duplicate records ────────────────────
print("\n[3] Database connection & duplicates")
DB_URL = os.getenv("DATABASE_URL", "")
if not DB_URL:
    print("    DATABASE_URL not set — skipping")
else:
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        print("    Connection     : OK")
        cursor = conn.cursor()

        # Count total records
        cursor.execute('SELECT COUNT(*) FROM "client_Requirements";')
        total = cursor.fetchone()[0]
        print(f"    Total records  : {total}")

        # Find duplicate lead_ids
        cursor.execute("""
            SELECT lead_id, COUNT(*) as cnt
            FROM "client_Requirements"
            WHERE lead_id IS NOT NULL AND lead_id != ''
            GROUP BY lead_id
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
            LIMIT 10;
        """)
        dupes = cursor.fetchall()
        if dupes:
            print(f"    Duplicates     : FOUND — {len(dupes)} lead_id(s) with multiple rows")
            for lead_id, cnt in dupes:
                print(f"                     lead_id={lead_id!r}  rows={cnt}")
        else:
            print("    Duplicates     : None found")

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"    Connection     : FAILED — {e}")

# ── 4. Package versions ───────────────────────────────────────────
print("\n[4] Key package versions")
packages = [
    "livekit", "livekit_agents", "livekit_plugins_sarvam",
    "livekit_plugins_anthropic", "livekit_plugins_silero",
    "psycopg2", "websockets",
]
for pkg in packages:
    try:
        mod = __import__(pkg.replace("-", "_"))
        ver = getattr(mod, "__version__", "unknown")
        print(f"    {pkg:<35} {ver}")
    except ImportError:
        print(f"    {pkg:<35} NOT INSTALLED")

print("\n" + "=" * 60)
print("DIAGNOSTICS COMPLETE")
print("=" * 60)
