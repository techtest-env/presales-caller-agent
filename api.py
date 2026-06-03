import asyncio
import sys
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Relai Caller Agent API")

class CallRequest(BaseModel):
    userid: str
    name: str
    phone: str

@app.post("/call")
async def trigger_call(request: CallRequest):
    result = await asyncio.create_subprocess_exec(
        sys.executable, "make_call.py",
        "--userid", request.userid,
        "--name", request.name,
        "--phone", request.phone,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await result.communicate()
    if result.returncode != 0:
        error_detail = stderr.decode().strip() or stdout.decode().strip() or "make_call.py exited with no output"
        raise HTTPException(status_code=500, detail=f"Failed to dispatch call: {error_detail}")

    return {"status": "success", "message": "Call dispatched"}

@app.get("/")
def health_check():
    return {"status": "healthy", "service": "relai-caller-agent"}