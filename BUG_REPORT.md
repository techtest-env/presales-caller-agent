# Bug Report - Relai Caller Agent

## Critical Bugs

### 1. **Missing Import in agent.py** ⚠️ CRITICAL
**File:** [agent.py](agent.py#L334)  
**Issue:** The `copy` module is imported inside the `entrypoint` function instead of at the top of the file.
```python
# Current (Line 334):
import copy
stt_opts = copy.deepcopy(...)
```
**Should be:** Add `import copy` at the top with other imports (line 1-25)
**Impact:** While this works, it's poor practice and could cause import timing issues.

---

### 2. **Missing push_to_db Import** ⚠️ CRITICAL
**File:** [agent.py](agent.py#L195)  
**Issue:** The code calls `push_to_db.push_single_to_db()` but never imports the module.
```python
# Line 195 in _save_and_push():
inserted_id = push_to_db.push_single_to_db(lead_data)
```
**Should be:** Add at top of agent.py:
```python
import push_to_db
```
**Impact:** This will cause a **NameError** at runtime when trying to save call data.

---

### 3. **Missing Config Attributes** ⚠️ CRITICAL
**File:** [agent.py](agent.py#L48-L50)  
**Issue:** The `_build_tts()` function references config attributes that don't exist in [config.py](config.py).
```python
# Lines 48-50:
if provider == "cartesia":
    model = os.getenv("CARTESIA_TTS_MODEL", config.CARTESIA_MODEL)  # ❌ NOT DEFINED
    voice = os.getenv("CARTESIA_TTS_VOICE", config.CARTESIA_VOICE)  # ❌ NOT DEFINED
```
**Config.py only has:**
- `DEFAULT_TTS_PROVIDER`
- `DEFAULT_TTS_VOICE`
- `SARVAM_MODEL`
- `SARVAM_LANGUAGE`
- `SARVAM_MODEL`

**Impact:** If Cartesia is selected as TTS provider, the code will crash with `AttributeError`.

---

### 4. **Groq LLM Configuration Bug** 🐛
**File:** [agent.py](agent.py#L81)  
**Issue:** When using Groq, the code checks for `"groq"` provider but references wrong env var.
```python
# Current:
model=os.getenv("GROQ_MODEL", config.DEFAULT_LLM_MODEL)
```
**Problem:** The env var in `.env` is:
```
LLM_PROVIDER=Groq  # Capital G
GROQ_API_KEY=...
```
But there's no `GROQ_MODEL` variable in `.env`. The code falls back to `config.DEFAULT_LLM_MODEL` which works, but the logic is confusing.

---

## High Priority Bugs

### 5. **Race Condition in end_call() Tool** 🔄
**File:** [agent.py](agent.py#L206-L260)  
**Issue:** Multiple potential race conditions:
- Line 206: `answers_collected` check happens before saving
- Line 213-216: `session.say()` is awaited but might timeout
- Line 256-258: Room deletion might fail silently after already shutting down

**Current code:**
```python
if answers_collected and self.session:
    # This might not complete before shutdown
    await session.say(closing_message, allow_interruptions=True)

self._save_and_push(...)  # Might save incomplete data

await self.ctx.api.room.delete_room(...)  # Might fail
self.ctx.shutdown()
```

**Impact:** Call data might not be fully captured, or TTS might be cut off.

---

### 6. **Double Disconnect Handler Registration** 🔄
**File:** [agent.py](agent.py#L430-L442)  
**Issue:** The `@ctx.room.on("disconnected")` handler calls `fnc_ctx._save_and_push()` but:
1. It can be called AFTER `end_call()` already saved data (causing duplicate saves)
2. The handler is defined AFTER the main agent starts, so early disconnects might not trigger it

**Current code:**
```python
@ctx.room.on("disconnected")
def on_disconnected():
    fnc_ctx._save_and_push({...})  # Saves again!
```

**The _call_ended flag tries to prevent this, but it's not thread-safe.**

**Impact:** Call results might be saved twice or data loss if room disconnects before handler is registered.

---

### 7. **Metadata Key Inconsistency** 🔄
**File:** [agent.py](agent.py#L300-L315)  
**Issue:** The code uses both `phone_number` and `phone` interchangeably:
```python
# Line 301:
phone_number = data.get("phone_number") or data.get("phone")

# But in make_call.py, only "phone" is used:
metadata=json.dumps({
    "phone": phone_number,  # ← Uses "phone", not "phone_number"
    "name": args.name,
    "userid": args.userid
})
```

This is fragile. If one system uses `phone_number` and another uses `phone`, lookups fail.

**Impact:** Phone number might not be parsed correctly depending on metadata source.

---

### 8. **Agent Greeting Not Used Correctly** 📞
**File:** [config.py](config.py#L35-L37)  
**Issue:** `INITIAL_GREETING` uses placeholder but the substitution might not work as expected:
```python
INITIAL_GREETING = "Hi, am I speaking with {{leadName}}?"
# Later in agent.py line 325:
custom_greeting = getattr(config, "INITIAL_GREETING", "...").replace("{{leadName}}", lead_name_str)
```

**Problem:** If `INITIAL_GREETING` is not defined in config (it IS, but getattr suggests fallback uncertainty), the agent uses wrong greeting.

**Also in agent.py line 331:** The fallback uses:
```python
await session.say(getattr(config, "fallback_greeting", "Hello!"), ...)
```

But `fallback_greeting` is defined in config.py line 38 with lowercase, which is inconsistent with Python naming conventions.

---

### 9. **STT Keyword Injection Bug** 🎤
**File:** [agent.py](agent.py#L332-L345)  
**Issue:** The keywords are being appended as tuples but Deepgram's keyword format might be different:
```python
for word in set([name] + name.split()):
    if word not in existing_words:
        existing_keywords.append((word, 3))  # Tuple format
```

**Problem:** The config.py defines keywords as tuples: `("BHK", 1)` which is correct for Deepgram, BUT the deduplication logic checks `kw[0]` which assumes all existing keywords are also tuples. If config has mixed formats, this could break.

**Impact:** Lead name might not be recognized by STT.

---

## Medium Priority Bugs

### 10. **Incomplete Error Handling in make_call.py** 📞
**File:** [make_call.py](make_call.py#L68-L78)  
**Issue:** The dispatch is created but the script doesn't wait for it to complete or check status:
```python
dispatch = await lk_api.agent_dispatch.create_dispatch(dispatch_request)

# Then immediately prints success without waiting for actual call completion
print(json.dumps({...}))
```

**Problem:** `make_call.py` returns immediately, so `main.py` can't know if the call actually started.

**Impact:** `main.py` waits for agent_process but if dispatch fails, the process hangs.

---

### 11. **Timing Issue in main.py** ⏱️
**File:** [main.py](main.py#L23-L28)  
**Issue:** Only 5-second sleep before making the call:
```python
agent_process = subprocess.Popen([venv_python, "agent.py", "start"], ...)
time.sleep(5)  # ← Might not be enough!
```

**Problem:** The agent takes time to:
1. Load all plugins (Sarvam, Deepgram, Groq, etc.)
2. Connect to LiveKit
3. Register with the worker pool

**If the agent isn't ready, `make_call.py` will fail to dispatch.**

**Impact:** Call might not dispatch properly, or timing-related failures.

---

### 12. **Database URL Configuration** 🗄️
**File:** [.env](.env#L25)  
**Issue:** The DATABASE_URL has a pool suffix:
```
DATABASE_URL="postgresql://user@host:6543/postgres"
```

**Problem:** The `:6543` port suggests a connection pooler (like PgBouncer), but:
- Standard PostgreSQL uses 5432
- If the pool isn't running, connections fail
- No error handling if DB is unreachable

**In push_to_db.py line 56:** Connection fails silently:
```python
except Exception as e:
    print(f"Error inserting data to DB: {e}")
    return None  # Silent failure
```

**Impact:** Call data might never be saved to the database.

---

### 13. **Incomplete on_disconnected Callback** 🔄
**File:** [agent.py](agent.py#L430-L442)  
**Issue:** The callback function body is incomplete in the provided code (lines show `...` where the body should be).

Looking at the summary, it should have a body but it's not fully visible. This might cause issues if the room disconnects unexpectedly.

---

### 14. **Missing Imports (Potential)** 📦
**File:** [agent.py](agent.py#L1-L30)  
**Check:** The following are used but verify they're imported:
- ✅ `json` - imported line 8
- ✅ `logging` - imported line 7
- ✅ `api` from livekit - imported line 14
- ⚠️ `push_to_db` - **NOT IMPORTED** (This is the critical bug #2)
- ⚠️ `copy` - imported inside function (should be at top)

---

## Low Priority Issues

### 15. **Inconsistent Logging** 📋
**File:** [agent.py](agent.py#L33)  
**Issue:** Logger name doesn't match agent name:
```python
logger = logging.getLogger("outbound-agent")  # But agent_name="outbound-caller"
```

Should be consistent for log filtering.

---

### 16. **Hardcoded Timeout Values** ⏱️
**File:** [agent.py](agent.py#L348-L354)  
**Issue:** Turn handling has hardcoded values with no way to configure them:
```python
turn_handling={
    "endpointing": {
        "min_delay": 0.4,
        "max_delay": 2.0
    },
    "interruption": {
        "enabled": True,
    }
}
```

These should be moved to config.py for flexibility.

---

## Summary

| Severity | Count | Issues |
|----------|-------|--------|
| 🔴 Critical | 4 | Missing imports, missing config attributes, config references |
| 🟠 High | 6 | Race conditions, metadata inconsistency, greeting logic, STT keywords |
| 🟡 Medium | 4 | Error handling, timing, DB configuration, callbacks |
| 🔵 Low | 2 | Logging consistency, hardcoded values |

## Recommended Fix Order
1. **First:** Add missing `push_to_db` import (Bug #2)
2. **Second:** Add missing config attributes or fix Cartesia TTS handling (Bug #3)
3. **Third:** Fix `copy` module import location (Bug #1)
4. **Fourth:** Add metadata key standardization (Bug #7)
5. **Fifth:** Increase timing in main.py (Bug #11)
6. **Sixth:** Add thread-safe disconnect handling (Bug #6)
