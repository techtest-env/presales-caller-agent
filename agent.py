import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import logging
import json
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from livekit import agents, api
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import (
    openai,
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

# TRUNK ID - Now loaded from config.py
# You can find this by running 'python setup_trunk.py --list' or checking LiveKit Dashboard 


def _build_tts(config_provider: str = None, config_voice: str = None):
    """Configure the Text-to-Speech provider based on env vars or dynamic config."""
    # Priority: Config > Env Var > Default
    provider = (config_provider or os.getenv("TTS_PROVIDER", config.DEFAULT_TTS_PROVIDER)).lower()
    
    # Sarvam voice names — if any of these are set, force Sarvam provider
    SARVAM_VOICES = {"anushka", "aravind", "amartya", "dhruv", "ritu", "meera", "arjun", "maya", "neel", "maitreyi", "karun", "arvind"}
    if config_voice and config_voice.lower() in SARVAM_VOICES:
        provider = "sarvam"

    if provider == "cartesia":
        logger.info("Using Cartesia TTS")
        model = os.getenv("CARTESIA_TTS_MODEL", config.CARTESIA_MODEL)
        voice = os.getenv("CARTESIA_TTS_VOICE", config.CARTESIA_VOICE)
        return cartesia.TTS(model=model, voice=voice)
    
    if provider == "sarvam":
        # Resolve voice: dynamic override > env var > config default
        voice = config_voice or os.getenv("SARVAM_VOICE", config.DEFAULT_TTS_VOICE)
        model = os.getenv("SARVAM_TTS_MODEL", config.SARVAM_MODEL)
        language = os.getenv("SARVAM_LANGUAGE", config.SARVAM_LANGUAGE)
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
        logger.info("Using Groq LLM")
        return openai.LLM(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY"),
            model=os.getenv("GROQ_MODEL", config.GROQ_MODEL),
            temperature=float(os.getenv("GROQ_TEMPERATURE", str(config.GROQ_TEMPERATURE))),
        )
    
    # Default to OpenAI
    logger.info("Using OpenAI LLM")
    return openai.LLM(model=config.DEFAULT_LLM_MODEL)



class TransferFunctions(llm.ToolContext):
    def __init__(self, ctx: agents.JobContext, phone_number: str = None, lead_id: str = None, name: str = None):
        super().__init__(tools=[])
        self.ctx = ctx
        self.phone_number = phone_number
        self.lead_id = lead_id
        self.name = name
        self.session = None  # Set after AgentSession is created
        # Track whether end_call was already invoked to prevent double-saves
        self._call_ended = False

    @llm.function_tool(description="Transfer the call to a human support agent or another phone number.")
    async def transfer_call(self, destination: Optional[str] = None):
        """
        Transfer the call.
        """
        if destination is None:
            destination = config.DEFAULT_TRANSFER_NUMBER
            if not destination:
                 return "Error: No default transfer number configured."
        if "@" not in destination:
            # If no domain is provided, append the SIP domain
            if config.SIP_DOMAIN:
                # Ensure clean number (strip tel: or sip: prefix if present but no domain)
                clean_dest = destination.replace("tel:", "").replace("sip:", "")
                destination = f"sip:{clean_dest}@{config.SIP_DOMAIN}"
            else:
                # Fallback to tel URI if no domain configured
                if not destination.startswith("tel:") and not destination.startswith("sip:"):
                     destination = f"tel:{destination}"
        elif not destination.startswith("sip:"):
             destination = f"sip:{destination}"
        
        logger.info(f"Transferring call to {destination}")
        
        # Determine the participant identity
        # For outbound calls initiated by this agent, the participant identity is typically "sip_<phone_number>"
        # For inbound, we might need to find the remote participant.
        participant_identity = None
        
        # If we stored the phone number from metadata, we can construct the identity
        if self.phone_number:
            participant_identity = f"sip_{self.phone_number}"
        else:
            # Try to find a participant that is NOT the agent
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
        os.makedirs("call_results", exist_ok=True)
        filename = f"call_results/lead_{self.lead_id or 'unknown'}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        try:
            with open(filename, "w") as f:
                json.dump(lead_data, f, indent=4)
            logger.info(f"Saved call details to {filename}")
        except Exception as e:
            logger.error(f"Failed to save call details: {e}")

        # Push to Database
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
        Call this once the conversation is over — the system handles the closing message.

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

        # For a completed qualification flow, speak the closing message explicitly.
        # session.say() awaits TTS completion before returning — no race condition.
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
            # Exit scenario: LLM already spoke the exit phrase, give it time to finish
            await asyncio.sleep(4)

        self._save_and_push({
            "property_type": property_type,
            "budget": budget,
            "areas": areas,
            "bhk": bhk,
            "possession_timeline": possession_timeline,
            "follow_up_time": follow_up_time,
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
    """
    An AI agent tailored for outbound calls.
    Attempts to be helpful and concise.
    """
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
    
    # parse the phone number AND config from the metadata
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
            config_dict.update(data) # Merge configs
    except Exception:
        logger.warning("No valid JSON metadata found in Room.")

    # Substitute leadName in instructions and greeting
    lead_name_str = name if name else "there"
    today_date_str = datetime.now().strftime("%A, %d %B %Y")  # e.g. "Sunday, 11 May 2026"
    custom_instructions = config.SYSTEM_PROMPT.replace("{{leadName}}", lead_name_str).replace("{{today_date}}", today_date_str)
    custom_greeting = config.INITIAL_GREETING.replace("{{leadName}}", lead_name_str)

    # Initialize function context
    fnc_ctx = TransferFunctions(ctx, phone_number, lead_id, name)

    # Build STT options dynamically — inject the lead's actual name into
    # Deepgram keywords so it recognizes their name correctly on
    # telephony-quality audio, regardless of who the lead is.
    import copy
    stt_opts = copy.deepcopy(
        getattr(config, "DEEPGRAM_OPTIONS", {"model": config.STT_MODEL, "language": config.STT_LANGUAGE})
    )
    if name:
        existing_keywords = stt_opts.get("keywords", [])
        existing_words = {kw[0] if isinstance(kw, tuple) else kw for kw in existing_keywords}
        # Boost full name and each word separately (handles multi-word names)
        for word in set([name] + name.split()):
            if word not in existing_words:
                existing_keywords.append((word, 3))
        stt_opts["keywords"] = existing_keywords
        logger.info(f"STT keyword boost added for lead name: {name}")

    session = AgentSession(
        vad=silero.VAD.load(
            min_speech_duration=0.2,   # Require 200ms of actual speech (ignores background noise/breaths)
            min_silence_duration=0.5   # LOWER LATENCY: Wait only 0.5s of silence before replying
        ),
        stt=deepgram.STT(**stt_opts),
        llm=_build_llm(config_dict.get("model_provider")),
        tts=_build_tts(config_dict.get("model_provider"), config_dict.get("voice_id")),
        turn_handling={
            "endpointing": {
                "min_delay": 0.4,
                "max_delay": 2.0
            },
            "interruption": {
                "enabled": True, # Still allows you to interrupt if you talk properly
            }
        }
    )

    # Give end_call tool access to session so it can await TTS before disconnecting
    fnc_ctx.session = session

    outbound_agent = OutboundAssistant(tools=list(fnc_ctx.function_tools.values()), custom_instructions=custom_instructions)
    # Start the session
    await session.start(
        room=ctx.room,
        agent=outbound_agent,
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVCTelephony(),
            close_on_disconnect=True, # Close room when agent disconnects
        ),
    )

    # Logic to dial out:
    # 1. If 'phone_number' is present, we MIGHT need to dial.
    # 2. Check if a SIP participant is already in the room (Dashboard dispatch case).
    
    should_dial = False
    if phone_number:
        # Check if any remote participant looks like our user (sip_PHONE)
        user_already_here = False
        for p in ctx.room.remote_participants.values():
            if f"sip_{phone_number}" in p.identity or "sip_" in p.identity:
                user_already_here = True
                break
        
        if not user_already_here:
            should_dial = True
            logger.info("User not in room. Agent will initiate dial-out.")
        else:
            logger.info("User already in room (Dashboard dispatched). output Only generated greeting.")

    if should_dial:
        logger.info(f"Initiating outbound SIP call to {phone_number}...")
        try:
            # Create a SIP participant to dial out
            # This effectively "calls" the phone number and brings them into this room
            # --- CONNECTING TO THE PHONE NETWORK ---
            # This step actually "dials" the number using Vobiz (SIP Trunk).
            # It invites the phone number into this digital room.
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
            
            # Have the agent speak it out loud (session.say automatically handles chat context)
            await session.say(custom_greeting, allow_interruptions=True)
            
        except Exception as e:
            logger.error(f"Failed to place outbound call: {e}")
            # Ensure we clean up if the call fails
            ctx.shutdown()
    else:
        # Fallback for inbound calls (if this agent is used for that) OR Dashboard calls where user is already there
        logger.info("Detecting if we should greet...")
        # Give a small delay for audio to stabilize if user just joined
        await session.say(config.fallback_greeting, allow_interruptions=True)

    # If the user hangs up abruptly (without end_call being triggered),
    # save whatever partial data we have so it's not lost.
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


if __name__ == "__main__":
    # The agent name "outbound-caller" is used by the dispatch script to find this worker
    agents.cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="outbound-caller", 
        )
    )
