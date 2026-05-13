import subprocess
import sys
import os
import glob
import json
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Relai Caller Agent API")

RESULTS_DIR = "/tmp/call_results" if sys.platform != "win32" else "call_results"
LOG_PATH = "/tmp/agent.log" if sys.platform != "win32" else "agent.log"

class CallRequest(BaseModel):
    userid: str
    name: str
    phone: str

@app.post("/call")
async def trigger_call(request: CallRequest):
    # Start the LiveKit agent worker
    agent_log = open(LOG_PATH, "a")
    agent_process = subprocess.Popen(
        [sys.executable, "agent.py", "start"],
        stdout=agent_log,
        stderr=subprocess.STDOUT
    )
    time.sleep(8)  # Wait for agent to boot and register with LiveKit

    # Dispatch the call
    result = subprocess.run(
        [sys.executable, "make_call.py",
         "--userid", request.userid,
         "--name", request.name,
         "--phone", request.phone],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        agent_process.terminate()
        raise HTTPException(status_code=500, detail=f"Failed to dispatch call: {result.stderr}")

    # Wait for the call to complete
    agent_process.wait()

    # Read the result file written by agent.py
    result_files = glob.glob(f"{RESULTS_DIR}/lead_{request.userid}_*.json")
    if not result_files:
        raise HTTPException(status_code=500, detail="Call ended but no result file was saved.")

    latest_file = max(result_files, key=os.path.getctime)
    with open(latest_file, "r") as f:
        call_output = json.load(f)
        call_output["call_status"] = "completed"
        return call_output

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "relai-caller-agent"}
