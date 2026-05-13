import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
import asyncio
import copy
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import (
    openai,
    anthropic,
    cartesia,
    deepgram,
    noise_cancellation,
    silero,
    sarvam,
)
from livekit.agents import llm
from typing import Annotated, Optional

# Load environment variables
load_dotenv(".env")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outbound-agent")

import config
import push_to_db


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
        hour = int(time_match.group(1))
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
    Detect language from customer speech and return the correct Sarvam language code.
    Scores Telugu and Hindi marker words; falls back to en-IN if neither detected.
    """
    telugu_markers = [
        "andi", "ra", "ga", "undi", "ledu", "meeru", "mee", "ema",
        "cheppandi", "avunu", "kaadu", "ante", "ayya", "akka", "anna",
        "babu", "ikkade", "akkade", "ela", "enduku", "chuda", "cheyyi",
        "vaddu", "sari", "okay ga", "telugu", "maku", "meeru", "miru"
    ]
    hindi_markers = [
        "haan", "nahi", "kya", "hai", "bhai", "theek", "acha", "nhi",
        "bol", "kar", "mujhe", "mera", "main", "aap", "tum", "yaar",
        "bilkul", "zaroor", "chaliye", "batao", "suniye", "ji", "huh",
        "woh", "yeh", "hindi", "dekhte", "sochte", "thoda"
    ]

    text_lower = text.lower()

    telugu_score = sum(1 for word in telugu_markers if word in text_lower)
    hindi_score = sum(1 for word in hindi_markers if word in text_lower)

    if telugu_score > hindi_score:
        logger.info(f"Language detected: Telugu (te-IN) | score={telugu_score}")
        return "te-IN"
    elif hindi_score > 0:
        logger.info(f"Language detected: Hindi (hi-IN) | score={hindi_score}")
        return "hi-IN"
    else:
        logger.info("Language detected: English (en-IN)")
        return "en-IN"


def _build_tts(config_provider: str = None, config_voice: str = None, language_code: str = None):
    """Configure the Text-to-Speech provider based on env vars or dynamic config."""
    provider = (config_provider or os.getenv("TTS_PROVIDER", config.DEFAULT_TTS_PROVIDER)).lower()

    # All known Sarvam voices across bulbul:v2 and bulbul:v3
    SARVAM_VOICES = {
        # bulbul:v2 voices
        "anushka", "manisha", "vidya", "arya", "abhilash", "karun", "hitesh",
        # bulbul:v3 voices
        "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
        "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
        "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait",
        "amelia", "sophia", "anand", "tanya", "tarun", "sunny", "mani", "gokul",
        "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali"
    }
    if config_voice and config_voice.lower() in SARVAM_VOICES:
        provider = "sarvam"

    if provider == "cartesia":
        logger.info("Using Cartesia TTS")
        model = os.getenv("CARTESIA_TTS_MODEL", config.CARTESIA_MODEL)
        voice = os.getenv("CARTESIA_TTS_VOICE", config.CARTESIA_VOICE)
        return cartesia.TTS(model=model, voice=voice)

    if provider == "sarvam":
        voice = config_voice or os.getenv("SARVAM_VOICE", config.DEFAULT_TTS_VOICE)
        model = os.getenv("SARVAM_TTS_MODEL", config.SARVAM_MODEL)
        # Priority: passed language_code > env var > config default
        language = language_code or os.getenv("SARVAM_LANGUAGE", config.SARVAM_LANGUAGE)
        api_key = os.getenv("SARVAM_API_KEY")
        logger.info(f"Using Sarvam TTS | Voice: {voice} | Model: {model} | Language: {language}")
        return sarvam.TTS(model=model, speaker=voice, target_language_code=language, api_key=api_key)

    if provider == "deepgram":
        logger.info("Using Deepgram TTS")
        model = os.getenv("DEEPGRAM_TTS_MODEL", "aura-asteria-en")
        return deepgram.TTS(model=model)

    # Default to OpenAI
    logger.info(f"Using OpenAI TTS (Voice: {config_voice})")
    model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
    voice = config_voice or os.getenv("OPENAI_TTS_VOICE", config.DEFAULT_TTS_VOICE)
    return openai.TTS(model=model, voice=voice)


def _build_llm(config_provider: str = None):
    """Configure the LLM provider based on config or env vars."""
    provider = (config_provider or os.getenv("LLM_PROVIDER", config.DEFAULT_LLM_PROVIDER)).lower()

    if provider == "groq":
        logger.info(f"Using Groq LLM | Model: {config.DEFAULT_LLM_MODEL}")
        return openai.LLM(
            base_url="https://api.groq.com/openai/v1",
            model=os.getenv("GROQ_MODEL", config.DEFAULT_LLM_MODEL),
            api_key=os.getenv("GROQ_API_KEY"),
            temperature=getattr(config, "GROQ_TEMPERATURE", getattr(config, "OPENAI_TEMPERATURE", 0.1)),
        )

    # Default to OpenAI
    logger.info(f"Using OpenAI LLM | Model: {config.DEFAULT_LLM_MODEL}")
    return openai.LLM(
        model=os.getenv("OPENAI_MODEL", config.DEFAULT_LLM_MODEL),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=getattr(config, "OPENAI_TEMPERATURE", getattr(config, "GROQ_TEMPERATURE", 0.1)),
    )


class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: agents.JobContext, phone_number: str = None, lead_id: str = None, name: str = None):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number
        self.lead_id = lead_id
        self.name = name
        self.session = None  # Set after AgentSession is created
        self._call_ended = False

    async def transfer_call(self, destination: Optional[str] = None):
        """Transfer the call (deactivated — decorator removed intentionally)."""
        if destination is None:
            destination = getattr(config, "DEFAULT_TRANSFER_NUMBER", None)
            if not destination:
                return "Error: No default transfer number configured."
        if "@" not in destination:
            if getattr(config, "SIP_DOMAIN", None):
                clean_dest = destination.replace("tel:", "").replace("sip:", "")
                destination = f"sip:{clean_dest}@{config.SIP_DOMAIN}"
            else:
                if not destination.startswith("tel:") and not destination.startswith("sip:"):
                    destination = f"tel:{destination}"
        elif not destination.startswith("sip:"):
            destination = f"sip:{destination}"

        logger.info(f"Transferring call to {destination}")

        participant_identity = None
        if self.phone_number:
            participant_identity = f"sip_{self.phone_number}"
        else:
            for p in self.ctx.room.remote_participants.values():
                participant_identity = p.identity
                break

        if not participant_identity:
            logger.error("Could not determine participant identity for transfer")
            return "Failed to transfer: could not identify the caller."

        try:
            logger.info(f"Transferring participant {participant_identity} to {destination}")
            await self.ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self.ctx.room.name,
                    participant_identity=participant_identity,
                    transfer_to=destination,
                    play_dialtone=False
                )
            )
            return "Transfer initiated successfully."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return f"Error executing transfer: {e}"

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

    @llm.function_tool(description="End the current call. Call this when all questions are answered OR when the user wants to stop. Pass all collected answers as arguments.")
    async def end_call(
        self,
        property_type: str = "",
        budget: str = "",
        areas: str = "",
        bhk: str = "",
        possession_timeline: str = "",
        follow_up_time: str = "",
        additional_notes: str = ""
    ):
        """
        Ends the current call and disconnects the user.

        Args:
            property_type: Type of property looking for (apartment or villa)
            budget: Budget range
            areas: Areas or localities interested in Hyderabad
            bhk: Number of BHK
            possession_timeline: Possession timeline
            follow_up_time: Exact date and time for follow-up
            additional_notes: Any additional information shared by the customer
        """
        logger.info("Agent is ending the call via end_call tool")

        formatted_follow_up = parse_follow_up_time(follow_up_time)

        answers_collected = any([property_type, budget, areas, bhk])
        if answers_collected and self.session:
            logger.info("Speaking closing message via session.say()...")
            await self.session.say(
                "Thank you so much! I have all the details I need. "
                "Our consultant will reach out soon with a personalised shortlist. "
                "Have a wonderful day!",
                allow_interruptions=False
            )
        else:
            await asyncio.sleep(4)

        self._save_and_push({
            "property_type": property_type,
            "budget": budget,
            "areas": areas,
            "bhk": bhk,
            "possession_timeline": possession_timeline,
            "follow_up_time": formatted_follow_up,
            "additional_notes": additional_notes
        })

        logger.info("Dropping SIP call by deleting the room...")
        try:
            await self.ctx.api.room.delete_room(
                api.DeleteRoomRequest(room=self.ctx.room.name)
            )
        except Exception as e:
            logger.error(f"Failed to delete room: {e}")

        self.ctx.shutdown()
        return "Call ended successfully."


class OutboundAssistant(Agent):
    """An AI agent tailored for outbound calls."""
    def __init__(self, tools: list, custom_instructions: str = None) -> None:
        super().__init__(
            instructions=custom_instructions or config.SYSTEM_PROMPT,
            tools=tools,
        )


async def entrypoint(ctx: agents.JobContext):
    """
    Main entrypoint for the agent.

    For outbound calls:
    1. Checks for 'phone_number' in the job metadata.
    2. Connects to the room.
    3. Initiates the SIP call to the phone number.
    4. Waits for answer before speaking.
    """
    logger.info(f"Connecting to room: {ctx.room.name}")

    phone_number = None
    lead_id = None
    name = None
    config_dict = {}

    # Check Job Metadata (Legacy/Dispatch)
    try:
        if ctx.job.metadata:
            data = json.loads(ctx.job.metadata)
            phone_number = data.get("phone_number") or data.get("phone")
            lead_id = data.get("lead_id") or data.get("userid")
            name = data.get("name")
            config_dict = data
    except Exception:
        pass

    # Check Room Metadata (Dashboard/Route.ts) - Overrides Job Metadata if present
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

    # Substitute leadName in instructions and greeting
    lead_name_str = name if name else "there"
    today_date_str = datetime.now().strftime("%A, %d %B %Y")
    custom_instructions = config.SYSTEM_PROMPT.replace("{{leadName}}", lead_name_str).replace("{{today_date}}", today_date_str)
    custom_greeting = getattr(config, "INITIAL_GREETING", "Hi, am I speaking with {{leadName}}?").replace("{{leadName}}", lead_name_str)

    # Initialize function context
    fnc_ctx = TransferFunctions(ctx, phone_number, lead_id, name)

    # Build STT options dynamically
    stt_opts = copy.deepcopy(
        getattr(config, "DEEPGRAM_OPTIONS", {
            "model": getattr(config, "STT_MODEL", "nova-2"),
            "language": getattr(config, "STT_LANGUAGE", "en")
        })
    )
    if name:
        existing_keywords = stt_opts.get("keywords", [])
        existing_words = {kw[0] if isinstance(kw, tuple) else kw for kw in existing_keywords}
        for word in set([name] + name.split()):
            if word not in existing_words:
                existing_keywords.append((word, 3))
        stt_opts["keywords"] = existing_keywords
        logger.info(f"STT keyword boost added for lead name: {name}")

    # --- Language tracking (mutable container so the closure can update it) ---
    # Start with en-IN for the greeting; switches automatically on first Telugu/Hindi response
    detected_lang = {"code": config_dict.get("language", config.SARVAM_LANGUAGE)}

    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.3
        ),
        stt=deepgram.STT(**stt_opts),
        llm=_build_llm(config_dict.get("model_provider")),
        tts=_build_tts(
            config_dict.get("model_provider"),
            config_dict.get("voice_id"),
            language_code=detected_lang["code"]
        ),
        turn_handling={
            "endpointing": {
                "min_delay": 0.2,
                "max_delay": 1.5
            },
            "interruption": {
                "enabled": True,
            }
        }
    )

    # Give end_call tool access to session so it can await TTS before disconnecting
    fnc_ctx.session = session

    # --- Dynamic language detection: swap TTS on every customer utterance ---
    @session.on("user_speech_committed")
    def on_user_speech(event):
        transcript = getattr(event, "transcript", "") or ""
        if not transcript.strip():
            return

        new_lang = _detect_language_code(transcript)

        # Only rebuild TTS if the language actually changed
        if new_lang != detected_lang["code"]:
            detected_lang["code"] = new_lang
            logger.info(f"Language switched to {new_lang}. Rebuilding TTS...")
            try:
                session.update_options(
                    tts=_build_tts(
                        config_dict.get("model_provider"),
                        config_dict.get("voice_id"),
                        language_code=new_lang
                    )
                )
                logger.info(f"TTS successfully updated to {new_lang}")
            except Exception as e:
                logger.error(f"Failed to update TTS language: {e}")

    outbound_agent = OutboundAssistant(
        tools=list(fnc_ctx.function_tools.values()),
        custom_instructions=custom_instructions
    )

    await session.start(
        room=ctx.room,
        agent=outbound_agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            close_on_disconnect=True,
        ),
    )

    should_dial = False
    if phone_number:
        user_already_here = False
        for p in ctx.room.remote_participants.values():
            if f"sip_{phone_number}" in p.identity or "sip_" in p.identity:
                user_already_here = True
                break

        if not user_already_here:
            should_dial = True
            logger.info("User not in room. Agent will initiate dial-out.")
        else:
            logger.info("User already in room (Dashboard dispatched). Only generated greeting.")

    if should_dial:
        logger.info(f"Initiating outbound SIP call to {phone_number}...")
        try:
            customer_name = name or "Customer"
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=config.SIP_TRUNK_ID,
                    sip_call_to=phone_number,
                    room_name=ctx.room.name,
                    participant_identity=f"sip_{phone_number}",
                    participant_name=customer_name,
                    display_name=config.DISPLAY_NAME,
                    wait_until_answered=True
                )
            )
            logger.info("Call answered! Agent is now listening.")
            await asyncio.sleep(0.5)
            logger.info(f"Speaking opening greeting for {lead_name_str}...")

# Part 1: Identity check — wait for them to confirm before continuing
            greeting_part1 = f"Hi, am I speaking with {lead_name_str}?"
            greeting_part2 = f"Hi! I'm Rishika from Relai. You were looking at properties in Hyderabad recently. Just have 6 quick questions to find your match. Is now a good time?"

            await session.say(greeting_part1, allow_interruptions=False)
            await asyncio.sleep(1.5)  # pause — feels natural, like waiting for them to say "yes"
            await session.say(greeting_part2, allow_interruptions=True)

            logger.info("Greeting spoken. Now waiting for user response to continue conversation.")
            # logger.info(f"Speaking opening greeting for {lead_name_str}...")
            # await session.say(custom_greeting, allow_interruptions=True)
            # logger.info("Greeting spoken. Now waiting for user response to continue conversation.")

        except Exception as e:
            logger.error(f"Failed to place outbound call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            ctx.shutdown()
    else:
        logger.info("User already in room. Speaking opening greeting.")
        greeting_part1 = f"Hi, am I speaking with {lead_name_str}?"
        greeting_part2 = f"Hi! I'm Priya from Relai. You were looking at properties in Hyderabad recently. Just have 6 quick questions to find your match. Is now a good time?"
        await session.say(greeting_part1, allow_interruptions=False)
        await asyncio.sleep(1.5)
        await session.say(greeting_part2, allow_interruptions=True)
        # logger.info("User already in room. Speaking opening greeting.")
        # await session.say(custom_greeting, allow_interruptions=True)

    # If the user hangs up abruptly, save whatever partial data we have
    @ctx.room.on("disconnected")
    def on_disconnected():
        logger.info("Room disconnected. Checking if call data needs to be saved...")
        fnc_ctx._save_and_push({
            "property_type": "",
            "budget": "",
            "areas": "",
            "bhk": "",
            "possession_timeline": "",
            "follow_up_time": "",
            "additional_notes": "Call ended abruptly by user hangup"
        })
        logger.info("Cleanly shutting down agent process.")
        ctx.shutdown()

    logger.info("Session is now running. Agent is waiting for user input...")
    await session.wait_for_inactive()


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller",
        )
    )