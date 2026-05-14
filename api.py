import subprocess
import sys
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Relai Caller Agent API")

LOG_PATH = "/tmp/agent.log" if sys.platform != "win32" else "agent.log"

class CallRequest(BaseModel):
    userid: str
    name: str
    phone: str

@app.post("/call")
async def trigger_call(request: CallRequest):
    agent_log = open(LOG_PATH, "a")
    agent_process = subprocess.Popen(
        [sys.executable, "agent.py", "start"],
        stdout=agent_log,
        stderr=subprocess.STDOUT
    )
    time.sleep(8)  # Wait for agent to boot and register with LiveKit

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

    return {"status": "success", "message": "Call dispatched"}

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "relai-caller-agent"}
