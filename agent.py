import sys
import asyncio

# Windows: ProactorEventLoop breaks WebSocket connections; Selector is stable.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
import re
import time
import tempfile
import httpx
from datetime import datetime, timedelta
from dotenv import load_dotenv

from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions, APIConnectOptions
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import (
    anthropic,
    noise_cancellation,
    silero,
    sarvam,
)
from livekit.agents import llm

# Load environment variables
load_dotenv(".env")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-agent")

logger.info(f"[REC] Supabase URL loaded: {os.environ.get('SUPABASE_URL', 'NOT SET')[:30]}...")
logger.info(f"[REC] Service role key loaded: {'SET' if os.getenv('SUPABASE_SERVICE_ROLE_KEY') else 'NOT SET'}")

import config


def parse_follow_up_time(time_str: str) -> str:
    """Convert natural date strings to DD-MM-YYYY-HH:MM format."""
    if not time_str or not time_str.strip():
        return ""

    time_str = time_str.lower().strip()
    now = datetime.now()

    # Extract time if mentioned (HH:MM or just hour)
    time_match = re.search(r'(\d{1,2}):?(\d{0,2})\s*(am|pm|am\.|pm\.)?', time_str)
    hour, minute, period = 10, 0, None
    if time_match:
        extracted_hour = int(time_match.group(1))
        if extracted_hour <= 23:  # ignore matches that are dates (e.g. "26" from "May 26")
            hour = extracted_hour
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            period = time_match.group(3)
            if period and 'pm' in period and hour < 12:
                hour += 12
            elif period and 'am' in period and hour == 12:
                hour = 0

    # Determine target date
    target_date = now

    if 'today' in time_str:
        target_date = now
    elif 'tomorrow' in time_str:
        target_date = now + timedelta(days=1)
    elif 'next week' in time_str or 'next monday' in time_str:
        target_date = now + timedelta(weeks=1)
    elif 'weekend' in time_str:
        days_ahead = 5 - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        target_date = now + timedelta(days=days_ahead)
    else:
        day_match = re.search(r'(\d{1,2})', time_str)
        month_match = re.search(
            r'(january|february|march|april|may|june|july|august|september|october|november|december'
            r'|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)',
            time_str
        )

        if day_match:
            day = int(day_match.group(1))
            month = now.month
            year = now.year

            if month_match:
                month_str = month_match.group(1)
                months = {
                    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
                    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
                    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
                }
                month = months.get(month_str, now.month)

            try:
                target_date = datetime(year, month, day, hour, minute)
                if target_date < now:
                    target_date = datetime(year + 1, month, day, hour, minute)
            except ValueError:
                target_date = now + timedelta(days=1)
        else:
            target_date = now + timedelta(days=1)
            target_date = target_date.replace(hour=hour, minute=minute)

    target_date = target_date.replace(hour=hour, minute=minute)
    return target_date.strftime("%d-%m-%Y-%H:%M")


def _detect_language_code(text: str) -> str:
    """
    Detect language from Sarvam STT output by counting Unicode script characters.
    STT with mode="transcribe" returns native Unicode, not romanized text.
    Telugu: U+0C00–U+0C7F, Devanagari (Hindi): U+0900–U+097F
    """
    if not text or not text.strip():
        return "en-IN"

    telugu_count = sum(1 for ch in text if 'ఀ' <= ch <= '౿')
    hindi_count = sum(1 for ch in text if 'ऀ' <= ch <= 'ॿ')

    total = len(text.strip())
    if total == 0:
        return "en-IN"

    if telugu_count / total > 0.15:
        logger.info("Language detected: Telugu (te-IN)")
        return "te-IN"
    elif hindi_count / total > 0.15:
        logger.info("Language detected: Hindi (hi-IN)")
        return "hi-IN"
    else:
        logger.info("Language detected: English (en-IN)")
        return "en-IN"


def _build_tts(config_voice: str = None, language_code: str = None):
    voice = config_voice or os.getenv("SARVAM_VOICE", config.DEFAULT_TTS_VOICE)
    model = os.getenv("SARVAM_TTS_MODEL", config.SARVAM_MODEL)
    language = language_code or os.getenv("SARVAM_LANGUAGE", config.SARVAM_LANGUAGE)
    api_key = os.getenv("SARVAM_API_KEY")
    logger.info(f"Using Sarvam TTS | Voice: {voice} | Model: {model} | Language: {language}")
    return sarvam.TTS(model=model, speaker=voice, target_language_code=language, api_key=api_key, pace=1.00)


# def _build_llm():  # GROQ - disabled
#     return openai.LLM(
#         base_url="https://api.groq.com/openai/v1",
#         model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
#         api_key=os.getenv("GROQ_API_KEY"),
#         temperature=0.1,
#     )

def _build_llm():
    logger.info(f"Using Anthropic LLM | Model: {config.DEFAULT_LLM_MODEL}")
    return anthropic.LLM(
        model=os.getenv("CLAUDE_MODEL", config.DEFAULT_LLM_MODEL),
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=getattr(config, "CLAUDE_TEMPERATURE", 0.1),
    )


# ── EGRESS RECORDING (replaces raw audio capture) ──────────
# Switched from raw WAV capture to LiveKit Egress
# Both sides of call recorded, pushed to Supabase S3 bucket
# ────────────────────────────────────────────────────────────

def _sb_headers():
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    return {"Authorization": f"Bearer {key}", "apikey": key}


async def _sb_upload(client, bucket, path, data, content_type):
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not base:
        logger.error("[REC] SUPABASE_URL not set — skipping upload")
        return False
    try:
        r = await client.post(
            f"{base}/storage/v1/object/{bucket}/{path}",
            content=data,
            headers={**_sb_headers(), "Content-Type": content_type},
            timeout=60.0,
        )
        if r.status_code not in (200, 201):
            logger.error(f"[REC] Upload {path} failed: {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.error(f"[REC] Upload {path} error: {e}")
        return False


async def _sb_signed_url(client, bucket, path, expires_in=31536000):
    base = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not base:
        return None
    try:
        r = await client.post(
            f"{base}/storage/v1/object/sign/{bucket}/{path}",
            json={"expiresIn": expires_in},
            headers={**_sb_headers(), "Content-Type": "application/json"},
            timeout=30.0,
        )
        if r.status_code == 200:
            payload = r.json()
            # Supabase returns signedURL as "/object/sign/..." (no /storage/v1 prefix)
            signed = payload.get("signedURL") or payload.get("signedUrl") or ""
            if signed and not signed.startswith("http"):
                signed = base + "/storage/v1" + signed
            return signed or None
        logger.error(f"[REC] Signed URL {path} failed: {r.status_code} body={r.text[:500]}")
        return None
    except Exception as e:
        logger.error(f"[REC] Signed URL {path} error: {e}")
        return None


def _run_db_migration():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("ALTER TABLE public.call_recordings ADD COLUMN IF NOT EXISTS transcript_url text;")
        conn.commit()
        cur.close()
        conn.close()
        logger.info("[REC] DB migration: transcript_url column ensured")
    except Exception as e:
        logger.error(f"[REC] DB migration error (non-fatal): {e}")


def _insert_recording_row(room_name, lead_name, recording_url, transcript_url, duration_seconds):
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        logger.error("[REC] DATABASE_URL not set — cannot insert call_recordings row")
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO public.call_recordings
               (room_name, lead_name, recording_url, transcript_url, duration_seconds, recorded_at)
               VALUES (%s, %s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')""",
            (room_name, lead_name, recording_url, transcript_url, duration_seconds),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[REC] Inserted call_recordings row for room {room_name}")
    except Exception as e:
        logger.error(f"[REC] DB insert error: {e}")


async def _upload_transcript_file(client, path, room_name, ts, month):
    """Upload transcript text file to Supabase Storage. Returns signed URL or None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
        s3_path = f"{month}/transcripts/{room_name}_{ts}.txt"
        ok = await _sb_upload(client, "call-recordings", s3_path, data, "text/plain; charset=utf-8")
        signed = await _sb_signed_url(client, "call-recordings", s3_path) if ok else None
        try:
            os.remove(path)
        except Exception:
            pass
        return signed
    except Exception as e:
        logger.error(f"[REC] Transcript upload error: {e}")
        return None


class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: agents.JobContext, phone_number: str = None, lead_id: str = None, name: str = None, language: str = "en-IN"):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number
        self.lead_id = lead_id
        self.name = name
        self.language = language
        self.session = None  # Set after AgentSession is created
        self._call_ended = False

    def _save_and_push(self, answers: dict):
        """Saves call results to a JSON file and pushes to the database."""
        if self._call_ended:
            logger.info("Call data already saved, skipping duplicate save.")
            return
        self._call_ended = True

        lead_data = {
            "lead_id": self.lead_id,
            "name": self.name,
            "phone_number": self.phone_number,
            "call_time": datetime.now().isoformat(),
            "answers": answers
        }
        results_dir = "/tmp/call_results" if sys.platform != "win32" else "call_results"
        os.makedirs(results_dir, exist_ok=True)
        filename = f"{results_dir}/lead_{self.lead_id or 'unknown'}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        try:
            with open(filename, "w") as f:
                json.dump(lead_data, f, indent=4)
            logger.info(f"Saved call details to {filename}")
        except Exception as e:
            logger.error(f"Failed to save call details: {e}")

        try:
            import push_to_db
            inserted_id = push_to_db.push_single_to_db(lead_data)
            if inserted_id:
                logger.info(f"Successfully pushed call details to DB with record ID {inserted_id}")
            else:
                logger.error("Failed to push call details to DB (returned None). Is DATABASE_URL set in .env?")
        except Exception as e:
            logger.error(f"Error calling push_to_db: {e}")

    @llm.function_tool(description="End the call. Use this in THREE situations: 1. EARLY EXIT — immediately if the user says wrong number, not interested, stop calling, or is clearly refusing. Do not ask any questions first. 2. COMPLETED — after all 6 questions have been answered. 3. BUSY — if the user says they cannot talk right now. Never hesitate to call this early if the situation calls for it.")
    async def end_call(
        self,
        property_type: str = "",
        budget: str = "",
        areas: str = "",
        bhk: str = "",
        possession_timeline: str = "",
        follow_up_time: str = "",
        additional_notes: str = "",
        call_outcome: str = "no_answer"
    ):
        """
        Ends the current call and disconnects the user.

        Args:
            property_type: Type of property looking for (apartment or villa)
            budget: Budget range
            areas: Areas or localities interested in Hyderabad
            bhk: Number of BHK
            possession_timeline: ready-to-move or construction timeline e.g. "6 months", "ready now"
            follow_up_time: callback day and time e.g. "tomorrow 2pm", "Wednesday evening"
            additional_notes: Any additional information shared by the customer
            call_outcome: Outcome of the call. One of: qualified, callback_requested, not_interested, wrong_number, wrong_number_new_lead, no_answer, abusive
        """
        def _clean(val: str) -> str:
            if not val:
                return ""
            val = re.sub(r'</?antml[^>]*>', '', val)
            val = re.sub(r'</?parameter[^>]*>', '', val)
            return val.strip()

        property_type = _clean(property_type)
        budget = _clean(budget)
        areas = _clean(areas)
        bhk = _clean(bhk)
        possession_timeline = _clean(possession_timeline)
        follow_up_time = _clean(follow_up_time)
        additional_notes = _clean(additional_notes)
        call_outcome = _clean(call_outcome)

        logger.info("Agent is ending the call via end_call tool")

        formatted_follow_up = parse_follow_up_time(follow_up_time)

        closing_messages = {
            "en-IN": "That's everything I needed. Our consultant will put together a personalised shortlist and be in touch soon. Have a wonderful day!",
            "te-IN": "చాలా ధన్యవాదాలు! మా కన్సల్టెంట్ త్వరలో మీకు కాల్ చేస్తారు. మంచి రోజు!",
            "hi-IN": "बहुत बहुत धन्यवाद! हमारे कंसल्टेंट जल्द ही आपसे संपर्क करेंगे। आपका दिन शुभ हो!",
        }
        decline_messages = {
            "en-IN": "No problem at all. Feel free to reach out whenever you are ready. Have a great day!",
            "te-IN": "పర్వాలేదు, మీకు అవసరమైనప్పుడు మేము ఉంటాము. మంచి రోజు!",
            "hi-IN": "कोई बात नहीं, जब भी ज़रूरत हो हम यहाँ हैं। आपका दिन शुभ हो!",
        }

        lang = self.language
        answers_collected = any([property_type, budget, areas, bhk])

        # Save before any await so on_disconnected can't race and save empty data first.
        self._save_and_push({
            "property_type": property_type,
            "budget": budget,
            "areas": areas,
            "bhk": bhk,
            "possession_timeline": possession_timeline,
            "follow_up_time": formatted_follow_up,
            "additional_notes": additional_notes,
            "call_outcome": call_outcome
        })

        if answers_collected and self.session:
            logger.info("Speaking closing message via session.say()...")
            await self.session.say(
                closing_messages.get(lang, closing_messages["en-IN"]),
                allow_interruptions=False
            )
        elif self.session:
            await self.session.say(
                decline_messages.get(lang, decline_messages["en-IN"]),
                allow_interruptions=False
            )

        logger.info("Dropping SIP call by deleting the room...")
        try:
            await self.ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=self.ctx.room.name)
            )
        except Exception as e:
            logger.error(f"Failed to delete room: {e}")

        return "Call ended successfully."


class OutboundAssistant(Agent):
    """An AI agent tailored for outbound calls."""
    def __init__(self, tools: list, custom_instructions: str = None) -> None:
        super().__init__(
            instructions=custom_instructions or config.SYSTEM_PROMPT,
            tools=tools,
        )


async def entrypoint(ctx: agents.JobContext):
    logger.info(f"Connecting to room: {ctx.room.name}")
    logger.info(f"[CONFIG] SIP_TRUNK_ID={config.SIP_TRUNK_ID!r} | LIVEKIT_URL={os.getenv('LIVEKIT_URL')!r}")
    await ctx.connect()
    _run_db_migration()

    phone_number = None
    lead_id = None
    name = None
    config_dict = {}

    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number") or data.get("phone")
            lead_id = data.get("lead_id") or data.get("userid")
            name = data.get("name")
            config_dict = data
    except Exception:
        pass

    try:
        if ctx.room.metadata:
            data = json.loads(ctx.room.metadata)
            if data.get("phone_number") or data.get("phone"):
                phone_number = data.get("phone_number") or data.get("phone")
            if data.get("lead_id") or data.get("userid"):
                lead_id = data.get("lead_id") or data.get("userid")
            if data.get("name"):
                name = data.get("name")
            config_dict.update(data)
    except Exception:
        logger.warning("No valid JSON metadata found in Room.")

    lead_name_str = name if name else "there"
    today_date_str = datetime.now().strftime("%A, %d %B %Y")
    custom_instructions = config.SYSTEM_PROMPT.replace("{{leadName}}", lead_name_str).replace("{{today_date}}", today_date_str)

    detected_lang = {"code": config_dict.get("language", config.SARVAM_LANGUAGE)}
    fnc_ctx = TransferFunctions(ctx, phone_number, lead_id, name, language=detected_lang["code"])

    # Determine if outbound dial-out is needed
    should_dial = False
    if phone_number:
        user_already_here = any(
            f"sip_{phone_number}" in p.identity or "sip_" in p.identity
            for p in ctx.room.remote_participants.values()
        )
        should_dial = not user_already_here
        logger.info("Outbound mode: will dial out." if should_dial else "Inbound mode: user already in room.")

    # For outbound: place the call and wait for participant to join BEFORE starting the session.
    # session.start() requires a participant in the room to fully initialize its activity —
    # calling it on an empty room leaves _activity=None and session.say() will fail.
    if should_dial:
        logger.info(f"Initiating outbound SIP call to {phone_number}...")
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=config.SIP_TRUNK_ID,
                    sip_call_to=phone_number,
                    room_name=ctx.room.name,
                    participant_identity=f"sip_{phone_number}",
                    participant_name=name or "Customer",
                    display_name=config.DISPLAY_NAME,
                    wait_until_answered=True
                )
            )
            logger.info("SIP call answered. Waiting for participant to join LiveKit room...")
            await ctx.wait_for_participant(identity=f"sip_{phone_number}")
            logger.info("Participant is in the room.")
        except Exception as e:
            logger.error(f"Failed to place outbound call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return

    # Build TTS early so we can prewarm the WebSocket pool before the first turn
    _tts = _build_tts(config_dict.get("voice_id"), language_code=detected_lang["code"])
    _tts.prewarm()
    logger.info("[TTS] Warm-up approach: prewarm() called — WebSocket pool pre-warmed")

    # Build session objects (event handlers registered before start)
    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.35,
            activation_threshold=0.6,
        ),
        stt=sarvam.STT(model="saaras:v3", mode="transcribe", api_key=os.getenv("SARVAM_API_KEY"), flush_signal=True),
        llm=_build_llm(),
        tts=_tts,
        conn_options=SessionConnectOptions(
            stt_conn_options=APIConnectOptions(timeout=10.0, retry_interval=2.0, max_retry=5),
            tts_conn_options=APIConnectOptions(timeout=10.0, retry_interval=2.0, max_retry=5),
        ),
        turn_handling={
            "endpointing": {"min_delay": 0.1, "max_delay": 1.0},
            "interruption": {"enabled": True, "min_words": 1},
        },
        turn_detection="stt",
        min_endpointing_delay=0.07,
    )

    fnc_ctx.session = session

    # ── Transcript init ───────────────────────────────────────
    _rec_ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    _rec_month = datetime.utcnow().strftime("%Y-%m")
    _rec_s3_path = f"{_rec_month}/{ctx.room.name}_{_rec_ts}.ogg"
    _transcript_path = os.path.join(tempfile.gettempdir(), f"transcript_{ctx.room.name}_{_rec_ts}.txt")
    _transcript_file = None
    try:
        _transcript_file = open(_transcript_path, "w", encoding="utf-8")
        logger.info(f"[REC] Transcript opened: {_transcript_path}")
    except Exception as e:
        logger.error(f"[REC] Transcript open error: {e}")

    def _write_turn(speaker, text):
        if not _transcript_file:
            return
        try:
            ts_str = datetime.utcnow().strftime("%H:%M:%S")
            _transcript_file.write(f"[{ts_str}] {speaker}: {text}\n")
            _transcript_file.flush()
        except Exception as exc:
            logger.error(f"[REC] Transcript write error: {exc}")

    _room_disconnected = asyncio.Event()

    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.info("Room disconnected. Checking if call data needs to be saved...")
        fnc_ctx._save_and_push({
            "property_type": "", "budget": "", "areas": "", "bhk": "",
            "possession_timeline": "", "follow_up_time": "",
            "additional_notes": "Call ended abruptly by user hangup",
            "call_outcome": "no_answer"
        })
        logger.info("Job complete. Agent worker remains active for next call.")
        _room_disconnected.set()

    @session.on("user_speech_committed")
    def on_user_speech(event):
        transcript = (
            getattr(event, "transcript", None)
            or getattr(event, "text", None)
            or ""
        )
        logger.info(f"[STT] transcript={transcript!r}")
        if not transcript.strip():
            return
        new_lang = _detect_language_code(transcript)
        logger.info(f"User said: {transcript!r} | lang_score → {new_lang}")
        if new_lang != detected_lang["code"]:
            detected_lang["code"] = new_lang
            fnc_ctx.language = new_lang
            logger.info(f"Language switched to {new_lang}. Rebuilding TTS...")
            try:
                session.update_options(tts=_build_tts(config_dict.get("voice_id"), language_code=new_lang))
                logger.info(f"TTS successfully updated to {new_lang}")
            except Exception as e:
                logger.error(f"Failed to update TTS language: {e}")

    @session.on("agent_speech_started")
    def on_agent_speech_started(_event):
        logger.info(f"[TIMER] TTS start: {time.time():.3f}")

    @session.on("agent_speech_committed")
    def on_agent_speech_done(_event):
        logger.info(f"[TIMER] TTS done: {time.time():.3f}")

    # ── Transcript capture ────────────────────────────────────
    @session.on("user_speech_committed")
    def on_user_speech_transcript(event):
        text = getattr(event, "transcript", None) or getattr(event, "text", None) or ""
        if str(text).strip():
            _write_turn("USER", str(text).strip())

    @session.on("agent_speech_committed")
    def on_agent_speech_transcript(event):
        msg = getattr(event, "message", None)
        text = ""
        if msg is not None:
            text = str(getattr(msg, "content", "") or "")
        if not text:
            text = str(getattr(event, "text", "") or "")
        if text.strip():
            _write_turn("AGENT", text.strip())

    outbound_agent = OutboundAssistant(
        tools=list(fnc_ctx.function_tools.values()),
        custom_instructions=custom_instructions
    )

    # Start session — participant is guaranteed to be in the room at this point
    await session.start(
        room=ctx.room,
        agent=outbound_agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            close_on_disconnect=True,
        ),
    )

    @session.on("error")
    def on_session_error(error):
        logger.error(f"Session error (will attempt recovery): {error}")
        # Don't let retryable errors kill the session

    # ── EGRESS RECORDING start ────────────────────────────────
    egress_id = None
    egress_start_time = None
    try:
        s3_output = api.S3Upload(
            access_key=os.getenv("SUPABASE_S3_ACCESS_KEY", ""),
            secret=os.getenv("SUPABASE_S3_SECRET_KEY", ""),
            bucket="call-recordings",
            region=os.getenv("SUPABASE_S3_REGION", "ap-northeast-1"),
            endpoint=os.getenv("SUPABASE_S3_ENDPOINT", ""),
            force_path_style=True,
        )
        file_output = api.EncodedFileOutput(
            filepath=_rec_s3_path,
            s3=s3_output,
            disable_manifest=True,
        )
        # CRITICAL: do NOT set layout= or custom_base_url= on RoomCompositeEgressRequest
        # Setting either forces the video pipeline even with audio_only=True
        # and will result in a silent recording
        egress_request = api.RoomCompositeEgressRequest(
            room_name=ctx.room.name,
            audio_only=True,
            file_outputs=[file_output],
        )
        egress_info = await ctx.api.egress.start_room_composite_egress(egress_request)
        egress_id = egress_info.egress_id
        egress_start_time = time.time()
        logger.info(f"[REC] Egress started: egress_id={egress_id} path={_rec_s3_path}")
    except Exception as e:
        logger.error(f"[REC] Egress start failed (non-fatal): {e}")

    # Only the identity-check line is hardcoded — LLM handles everything after the user's first response
    await session.say(f"Hi! Am I speaking with {lead_name_str}?", allow_interruptions=True)
    logger.info("Greeting spoken. Waiting for user response...")

    logger.info("Session is now running. Agent is waiting for user input...")
    await session.wait_for_inactive()
    # wait_for_inactive() returns when the session idles (e.g. after greeting).
    # Wait for the room to be fully torn down before stopping Egress and uploading.
    await _room_disconnected.wait()
    logger.info("[REC] Room confirmed disconnected — starting finalize.")

    # ── Close transcript ──────────────────────────────────────
    if _transcript_file:
        try:
            _transcript_file.close()
        except Exception:
            pass

    # ── Stop Egress + upload transcript + insert DB row ───────
    recording_url = None
    duration_seconds = 0

    if egress_id:
        try:
            stopped_info = await ctx.api.egress.stop_egress(
                api.StopEgressRequest(egress_id=egress_id)
            )
            if stopped_info.ended_at and stopped_info.started_at:
                duration_seconds = int(
                    (stopped_info.ended_at - stopped_info.started_at) / 1_000_000_000
                )
            elif egress_start_time:
                duration_seconds = int(time.time() - egress_start_time)
            logger.info(f"[REC] Egress stopped. Duration: {duration_seconds}s")
        except Exception as e:
            logger.error(f"[REC] Egress stop error: {e}")
            if egress_start_time:
                duration_seconds = int(time.time() - egress_start_time)

    async with httpx.AsyncClient() as client:
        if egress_id:
            try:
                recording_url = await _sb_signed_url(client, "call-recordings", _rec_s3_path)
            except Exception as e:
                logger.error(f"[REC] Recording signed URL error: {e}")
        transcript_url = await _upload_transcript_file(
            client, _transcript_path, ctx.room.name, _rec_ts, _rec_month
        )

    _insert_recording_row(
        ctx.room.name, lead_name_str,
        recording_url, transcript_url, duration_seconds,
    )


async def prewarm(proc: agents.JobProcess):
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("[PREWARM] VAD preloaded for fast cold start")


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="outbound-caller",
        )
    )