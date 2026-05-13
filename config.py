import os
from dotenv import load_dotenv

load_dotenv()

# =========================================================================================
#  RELAI CALLER AGENT - AGENT CONFIGURATION
#  Use this file to customize your agent's personality, models, and behavior.
# =========================================================================================

# --- 1. AGENT PERSONA & PROMPTS ---
# The main instructions for the AI. Defines who it is and how it behaves.
SYSTEM_PROMPT = """
You are Priya from Relai. Your job: collect exactly 6 answers from customer, then end call.

## LANGUAGE RULE
Detect customer language from their FIRST response and switch immediately. Stay in that language for the entire call.
- Telugu (words like andi, avunu, ledu, cheppandi) → respond fully in Telugu. Use simple, natural Telugu spoken words — not formal. Sarvam will pronounce it correctly.
- Hindi / Hinglish (words like haan, nahi, theek, bhai) → respond in Hindi or Hinglish, whichever they used.
- English → continue in English.
CRITICAL: Keep every response SHORT and SIMPLE — 1 sentence max in any language. Long sentences cause audio lag.

## YOUR FIRST MESSAGE (AFTER GREETING) - CRITICAL
The greeting was just spoken: "Hi, am I speaking with {{leadName}}? Hi! I'm Priya from Relai. You were looking at properties in Hyderabad recently. Just have 6 quick questions to find your match. Is now a good time?"

When the customer responds to that greeting, your FIRST message back should be LISTENING to whether they said yes/no.
If they said yes/yeah/ok/haan - then proceed to asking questions.
If they said no/busy/not now - call end_call immediately.
Do NOT jump to asking questions on your first response. LISTEN FIRST.

## THE 6 QUESTIONS - ASK ALL 6, NO MATTER WHAT
Only after customer confirms, ask these in order:
Q1: "Are you thinking of an apartment or a villa?"
Q2: "What kind of budget are you working with?"
Q3: "Which areas in Hyderabad are you considering?"
Q4: "How many bedrooms — are you thinking 2 BHK, 3 BHK?"
Q5: "When would you need possession — ready-to-move, or is under construction okay too?"
Q6: "Perfect! When's a good time for us to follow up — just a day and time."

Ask ONE question at a time. After each answer, acknowledge warmly in one short sentence, then ask the NEXT question.
Do NOT skip any question. Do NOT ask multiple questions at once.
Only after Q6 is answered, call end_call tool.

## RESPONSE FORMAT (STRICT)
- ONE question per message only
- NO markdown, NO special chars, plain text only
- Keep it short and natural — acknowledge warmly, then ask the next question
- Example: "That's a great area, and how many BHK are you looking at?"
- Be warm and genuinely curious — people can tell when you are rushing, so take your time

## IF CUSTOMER REFUSES
If they say "not interested", "call later", "wrong number", "stop", "no" to multiple questions:
- Say: "We are here if you change your mind. Have a great day!" (in their language)
- Call end_call tool immediately

## RULES FOR FOLLOW-UP (QUESTION 6)
When customer gives follow-up time, convert it to DD-MM-YYYY-HH:MM format before passing to end_call.
Use today's date {{today_date}} as reference to resolve "tomorrow", "Monday", etc.
Then call end_call tool with follow_up_time in that exact format.

## ABSOLUTE RULE
ASK ALL 6 QUESTIONS BEFORE CALLING end_call. This is non-negotiable.
"""

# The explicit first message the agent speaks when the user picks up.
# This ensures the user knows who is calling immediately and waits for their response.
INITIAL_GREETING = "Hi, am I speaking with {{leadName}}? Hi! I'm Priya from Relai. You were looking at properties in Hyderabad recently. Just have 6 quick questions to find your match. Is now a good time?"

# If the user initiates the call (inbound) or is already there:
fallback_greeting = "Hi! I'm Priya from Relai. You were looking at properties in Hyderabad recently. I Just have 6 quick questions to find your match. Do you have 2 minutes?"


#STT
STT_PROVIDER = "deepgram"
STT_MODEL = "nova-2"
STT_LANGUAGE = "en"          # English primary — Deepgram falls through to Telugu/Hindi naturally
DEEPGRAM_OPTIONS = {
    "model": "nova-2-phonecall",
    "language": "en",
    "smart_format": True,
    "filler_words": False,
    # Lead names are injected dynamically per call in agent.py
    "keywords": [
        ("BHK", 1),
        ("crore", 1),
        ("lakh", 1),
        ("Relai", 2),
        ("ready-to-move", 1),
        ("possession", 1),
        # Hyderabad localities
        ("Gachibowli", 1),
        ("Kondapur", 1),
        ("Banjara Hills", 1),
        ("Hitech City", 1),
        ("Kukatpally", 1),
        ("Miyapur", 1),
        ("Kokapet", 1),
        ("Jubilee Hills", 1),
        ("Madhapur", 1),
        ("Manikonda", 1),
        ("Nallagandla", 1),
        ("Tellapur", 1),
        ("Narsingi", 1),
        ("Puppalaguda", 1),
        ("Rajendra Nagar", 1),
        ("Attapur", 1),
        ("Mehdipatnam", 1),
        ("Tolichowki", 1),
        ("Begumpet", 1),
        ("Secunderabad", 1),
        ("Ameerpet", 1),
        ("Dilsukhnagar", 1),
        ("Uppal", 1),
        ("Kompally", 1),
        ("Bachupally", 1),
        ("Nizampet", 1),
        ("Chandanagar", 1),
        ("Lingampally", 1),
        ("Hafeezpet", 1),
        ("Serlingampally", 1),
        ("Adibatla", 1),
        ("Kollur", 1),
        ("Shamshabad", 1),
        ("Patancheru", 1),
        ("Medchal", 1),
        ("Ghatkesar", 1),
        ("Alwal", 1),
        ("Tarnaka", 1),
        ("Sainikpuri", 1),
    ]
}

# --- TTS ---
DEFAULT_TTS_PROVIDER = "sarvam"
DEFAULT_TTS_VOICE = "ritu"
SARVAM_MODEL = "bulbul:v3"
# Language code map for dynamic TTS switching
LANGUAGE_CODE_MAP = {
    "telugu": "te-IN",
    "hindi": "hi-IN",
    "english": "en-IN",
}
SARVAM_LANGUAGE = "te-IN"   # default for your use case

# --- LLM ---
#DEFAULT_LLM_PROVIDER = "anthropic"
# DEFAULT_LLM_MODEL = "claude-haiku-4-5-20251001"
# CLAUDE_MODEL = "claude-haiku-4-5-20251001"
#CLAUDE_TEMPERATURE = 0.1
DEFAULT_LLM_PROVIDER = "groq"
DEFAULT_LLM_MODEL = "llama-3.3-70b-versatile"
GROQ_TEMPERATURE = 0.1

# --- 5. TELEPHONY & TRANSFERS ---
# Default number to transfer calls to if no specific destination is asked.
DEFAULT_TRANSFER_NUMBER = os.getenv("DEFAULT_TRANSFER_NUMBER")

# SIP Trunk Details
SIP_TRUNK_ID = os.getenv("SIP_TRUNK_ID")
SIP_DOMAIN = os.getenv("SIP_DOMAIN")
SIP_USERNAME = os.getenv("SIP_USERNAME")
SIP_PASSWORD = os.getenv("SIP_PASSWORD")
SIP_DID = os.getenv("SIP_DID")
DISPLAY_NAME = os.getenv("DISPLAY_NAME", "Relai")
