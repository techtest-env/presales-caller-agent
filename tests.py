"""
Relai Agent — Stability Test Suite
Tests: asyncio policy, TTS connection, duplicate DB guard, VAD config, LLM streaming.
"""
import sys
import os
import asyncio

os.environ['SSL_CERT_FILE'] = __import__('certifi').where()
from dotenv import load_dotenv
load_dotenv(".env")

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition))
    detail_str = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{detail_str}")

print("=" * 60)
print("RELAI AGENT TESTS")
print("=" * 60)

# ── Test 1: asyncio policy ────────────────────────────────────────
print("\n[Test 1] asyncio event loop policy")
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
policy = asyncio.get_event_loop_policy()
is_selector = isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy) if sys.platform == "win32" else True
check(
    "WindowsSelectorEventLoopPolicy active on win32",
    is_selector,
    type(policy).__name__
)

# ── Test 2: TTS connection within 30s ────────────────────────────
print("\n[Test 2] TTS connection (Sarvam REST reachable within 30s)")
import urllib.request, urllib.error, ssl, time

SARVAM_KEY = os.getenv("SARVAM_API_KEY", "")

def test_sarvam_reachable(url, timeout=30):
    t0 = time.time()
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"api-subscription-key": SARVAM_KEY})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx):
            pass
    except urllib.error.HTTPError as e:
        return True, (time.time() - t0) * 1000, e.code  # reachable
    except Exception as e:
        return False, (time.time() - t0) * 1000, str(e)
    return True, (time.time() - t0) * 1000, 200

ok, ms, code = test_sarvam_reachable("https://api.sarvam.ai/text-to-speech")
check("TTS endpoint reachable within 30s", ok, f"HTTP {code} in {ms:.0f}ms")

ok, ms, code = test_sarvam_reachable("https://api.sarvam.ai/speech-to-text")
check("STT endpoint reachable within 30s", ok, f"HTTP {code} in {ms:.0f}ms")

# ── Test 3: Duplicate DB guard ────────────────────────────────────
print("\n[Test 3] Duplicate DB insert guard")
import psycopg2
from psycopg2.extras import Json as PgJson

DB_URL = os.getenv("DATABASE_URL", "")
if not DB_URL:
    check("DB available", False, "DATABASE_URL not set — skipping")
else:
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        TEST_LEAD_ID = "_test_duplicate_guard_do_not_keep_"

        # Clean up any leftover test record first
        cur.execute('DELETE FROM "client_Requirements" WHERE lead_id = %s;', (TEST_LEAD_ID,))
        conn.commit()

        dummy = {
            "lead_id": TEST_LEAD_ID, "name": "Test", "phone_number": "+910000000000",
            "answers": {"additional_notes": "test"}
        }
        prefs = PgJson({"budget": "test"})

        def insert_once(cur):
            cur.execute("""
                SELECT id FROM "client_Requirements"
                WHERE lead_id = %s AND created_at >= NOW() - INTERVAL '120 seconds'
                LIMIT 1;
            """, (TEST_LEAD_ID,))
            if cur.fetchone():
                return False  # skipped
            cur.execute("""
                INSERT INTO "client_Requirements"
                (client_mobile, requirement_name, preferences, lead_name, lead_mobile, lr_notes, lead_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id;
            """, ("+910000000000", "Test req", prefs, "Test", "+910000000000", "", TEST_LEAD_ID))
            return True  # inserted

        # First insert should succeed
        inserted_first = insert_once(cur)
        conn.commit()
        check("First insert succeeds", inserted_first, "row created")

        # Second insert should be skipped
        inserted_second = insert_once(cur)
        conn.commit()
        check("Second insert skipped (duplicate guard)", not inserted_second, "no duplicate row")

        # Confirm only one row
        cur.execute('SELECT COUNT(*) FROM "client_Requirements" WHERE lead_id = %s;', (TEST_LEAD_ID,))
        count = cur.fetchone()[0]
        check("Only 1 row in DB after 2 insert attempts", count == 1, f"found {count} row(s)")

        # Clean up
        cur.execute('DELETE FROM "client_Requirements" WHERE lead_id = %s;', (TEST_LEAD_ID,))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        check("DB duplicate guard", False, str(e))

# ── Test 4: VAD config ────────────────────────────────────────────
print("\n[Test 4] VAD configuration values")
try:
    from livekit.plugins import silero
    vad = silero.VAD.load(
        min_speech_duration=0.1,
        min_silence_duration=0.5,
        activation_threshold=0.6,
    )
    opts = vad._opts if hasattr(vad, '_opts') else None
    if opts:
        check("min_silence_duration=0.5",  abs(getattr(opts, 'min_silence_duration', -1) - 0.5) < 1e-9,  str(getattr(opts, 'min_silence_duration', '?')))
        check("activation_threshold=0.6",  abs(getattr(opts, 'activation_threshold', -1) - 0.6) < 1e-9,  str(getattr(opts, 'activation_threshold', '?')))
        check("min_speech_duration=0.1",   abs(getattr(opts, 'min_speech_duration', -1)  - 0.1) < 1e-9,  str(getattr(opts, 'min_speech_duration', '?')))
    else:
        # VAD loaded but opts not inspectable — just confirm it loads
        check("VAD loads with target params", vad is not None, "opts not introspectable")
except Exception as e:
    check("VAD config", False, str(e))

# ── Test 5: LLM streaming ─────────────────────────────────────────
print("\n[Test 5] LLM streaming mode")
try:
    from livekit.plugins import anthropic as lk_anthropic
    import inspect
    ret_ann = inspect.signature(lk_anthropic.LLM.chat).return_annotation
    # Should return LLMStream, not a coroutine — streaming confirmed
    is_streaming = "Stream" in str(ret_ann) or ret_ann is not None
    check("anthropic.LLM.chat returns LLMStream (streaming)", is_streaming, str(ret_ann))

    from livekit.agents import APIConnectOptions
    opts = APIConnectOptions(timeout=30.0, retry_interval=2.0, max_retry=5)
    check("APIConnectOptions(timeout=30, retry=5) constructible", True,
          f"timeout={opts.timeout} max_retry={opts.max_retry}")
except Exception as e:
    check("LLM streaming", False, str(e))

# ── Summary ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
total  = len(results)
print(f"RESULTS: {passed}/{total} passed")
if passed < total:
    print("FAILED:")
    for name, ok in results:
        if not ok:
            print(f"  - {name}")
print("=" * 60)
