import subprocess
import sys
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Relai Caller Agent API")

agent_process = None

def start_agent():
    global agent_process
    print("Starting LiveKit agent worker...")
    agent_log = open("agent.log", "a")
    agent_process = subprocess.Popen(
        [sys.executable, "agent.py", "start"],
        stdout=agent_log,
        stderr=subprocess.STDOUT
    )
    print("LiveKit agent worker started.")

@app.on_event("startup")
def startup_event():
    # Start the persistent agent worker in the background when the API boots up
    start_agent()

@app.on_event("shutdown")
def shutdown_event():
    global agent_process
    if agent_process:
        print("Shutting down agent process...")
        agent_process.terminate()

class CallRequest(BaseModel):
    userid: str
    name: str
    phone: str

@app.post("/call")
async def trigger_call(request: CallRequest):
    """
    Endpoint for n8n to trigger a call.
    """
    try:
        # Dispatch the call via make_call.py
        result = subprocess.run(
            [sys.executable, "make_call.py", "--userid", request.userid, "--name", request.name, "--phone", request.phone],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to dispatch call: {result.stderr}")
        
        return {
            "status": "success", 
            "message": f"Call successfully dispatched to {request.phone}", 
            "userid": request.userid
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "relai-caller-agent"}
