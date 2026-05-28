import argparse
import subprocess
import sys
import json
import time
import threading
import glob
import os

def run_command(cmd):
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )
    return result


def kill_port_8081():
    """Kill any stale process holding port 8081 before starting a fresh agent."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-NetTCPConnection -LocalPort 8081 -ErrorAction SilentlyContinue).OwningProcess"
             " | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, text=True, timeout=6
        )
        time.sleep(1)  # Give OS time to release the port
    except Exception:
        pass  # Port was not in use — that's fine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--userid", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--phone", required=True)
    args = parser.parse_args()

    venv_python = r".\venv\Scripts\python.exe"

    # Step 1 — Kill any stale agent process on port 8081, then start a fresh one.
    kill_port_8081()

    def tail_log(filename):
        with open(filename, "r") as f:
            f.seek(0, 2)  # Start tailing from the end
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue
                sys.stderr.write(f"[AGENT LOG] {line}")
                sys.stderr.flush()

    agent_log = open("agent.log", "a")  # Create/open file before thread tries to read it
    agent_log.flush()

    # Start live log streaming in the background (to stderr so stdout stays clean for n8n)
    threading.Thread(target=tail_log, args=("agent.log",), daemon=True).start()

    agent_process = subprocess.Popen(
        [venv_python, "agent.py", "start"],
        stdout=agent_log,
        stderr=subprocess.STDOUT
    )
    time.sleep(8)  # Increased from 5s - give agent time to boot and register with LiveKit

    # Step 2 — Make the call
    call_start_time = time.time()

    call_result = run_command([
        venv_python, "make_call.py",
        "--userid", args.userid,
        "--name", args.name,
        "--phone", args.phone
    ])
    if call_result.returncode != 0:
        print(json.dumps({
            "userid": args.userid,
            "call_status": "failed",
            "error": f"make_call.py failed: {call_result.stderr}"
        }))
        sys.exit(1)

    # Step 3 — Poll for the result file written by the agent at call end.
    # The agent process stays alive after the call, so we can't use wait().
    result_files = []
    deadline = call_start_time + 600  # 10-minute max call duration
    while time.time() < deadline:
        files = glob.glob(f"call_results/lead_{args.userid}_*.json")
        new_files = [f for f in files if os.path.getctime(f) >= call_start_time]
        if new_files:
            result_files = new_files
            break
        time.sleep(2)

    if not result_files:
        print(json.dumps({
            "userid": args.userid,
            "call_status": "failed",
            "error": "Call ended but no result file was saved."
        }))
        sys.exit(1)

    # Get the most recent file if there are multiple
    latest_file = max(result_files, key=os.path.getctime)
    try:
        with open(latest_file, "r") as f:
            call_output = json.load(f)
            call_output["call_status"] = "completed"
            print(json.dumps(call_output))
    except Exception as e:
        print(json.dumps({
            "userid": args.userid,
            "call_status": "failed",
            "error": f"Failed to read result: {e}"
        }))


if __name__ == "__main__":
    main()
