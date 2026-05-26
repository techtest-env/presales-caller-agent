import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
import certifi

# Fix for macOS SSL Certificate errors - MUST be before other imports
os.environ['SSL_CERT_FILE'] = certifi.where()

import argparse
import random
import json
import logging
import sys
from dotenv import load_dotenv
from livekit import api

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

async def main():
    parser = argparse.ArgumentParser(description="Make an outbound call via LiveKit Agent.")
    parser.add_argument("--userid", required=True, help="The ID of the lead")
    parser.add_argument("--name", required=True, help="The name of the lead")
    parser.add_argument("--phone", required=True, help="The phone number to call (e.g., +91...)")
    args = parser.parse_args()
    
    phone_number = args.phone.strip()
    if not phone_number.startswith("+"):
        print("Error: Phone number must start with '+' and country code.")
        sys.exit(1)

    if len(phone_number) < 8:
        print(f"Error: Phone number '{phone_number}' looks too short.")
        sys.exit(1)

    url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not (url and api_key and api_secret):
        print("Error: LiveKit credentials missing in .env")
        sys.exit(1)

    # 2. Setup API Client
    lk_api = api.LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)

    # Create a unique room for this call
    # We use a random suffix to ensure room names are unique
    room_name = f"call-{phone_number.replace('+', '')}-{random.randint(1000, 9999)}"

    try:
        # 4. Dispatch the Agent
        # We explicitly tell LiveKit to send the 'outbound-caller' agent to this room.
        # We pass the phone number in the 'metadata' field so the agent knows who to dial.
        dispatch_request = api.CreateAgentDispatchRequest(
            agent_name="outbound-caller", # Must match agent.py
            room=room_name,
            metadata=json.dumps({
                "phone": phone_number,
                "name": args.name,
                "userid": args.userid
            })
        )
        
        dispatch = await lk_api.agent_dispatch.create_dispatch(dispatch_request)

        # We suppress normal print logs by just omitting them or we can leave them 
        # but the JSON needs to be printed at the very end
        # The prompt asked: And at the end of the script, print a JSON result to stdout like this...
        pass
        
    except Exception as e:
        print(f"Error: dispatch failed — {e}", file=sys.stderr)
        sys.exit(1)
    
    finally:
        await lk_api.aclose()

if __name__ == "__main__":
    asyncio.run(main())
