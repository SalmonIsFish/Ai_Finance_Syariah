import sys
import subprocess
import os
import traceback
import json
import time
import glob
import threading
from datetime import datetime, timezone

def read_stdin_with_timeout(timeout=1.0):
    result = {"raw": ""}
    def _read():
        try:
            result["raw"] = sys.stdin.read()
        except Exception:
            pass
            
    if sys.stdin.isatty():
        return ""
        
    t = threading.Thread(target=_read)
    t.daemon = True
    t.start()
    t.join(timeout)
    if t.is_alive():
        return ""
    return result["raw"]

def main():
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    agents_dir = os.path.dirname(scripts_dir)
    repo_root = os.path.dirname(agents_dir)
    github2_dir = os.path.dirname(repo_root)
    vault_dir = os.path.join(github2_dir, "amanah-trader-vault")
    
    log_file = os.path.join(github2_dir, "vault_sync_error.log")
    seen_log = os.path.join(agents_dir, "conversation_ids_seen.log")
    
    now = time.time()
    for lock in glob.glob(os.path.join(agents_dir, ".pull_lock_*")):
        try:
            if os.path.isfile(lock) and os.stat(lock).st_mtime < now - 86400:
                os.remove(lock)
        except Exception:
            pass
    
    conv_id = None
    try:
        raw = read_stdin_with_timeout(1.0)
        if raw.strip():
            payload = json.loads(raw)
            conv_id = payload.get("session_id")
    except Exception as e:
        with open(log_file, "a") as f:
            f.write(f"Warning: Failed to parse hook payload from stdin: {e}\n")
            
    # Log the ID to our independently verifiable log
    timestamp = datetime.now(timezone.utc).isoformat()
    with open(seen_log, "a") as f:
        f.write(f"[{timestamp}] Seen ID: {conv_id if conv_id else 'None (fallback)'}\n")
            
    if not conv_id:
        conv_id = f"fallback_pid_{os.getppid()}"
        with open(log_file, "a") as f:
            f.write(f"Warning: No session_id found in payload. Falling back to session ID {conv_id}\n")
            
    lock_file = os.path.join(agents_dir, f".pull_lock_{conv_id}")
    
    if not os.path.exists(lock_file):
        try:
            pull_res = subprocess.run(
                ["git", "-C", vault_dir, "pull", "--rebase"], 
                capture_output=True, 
                text=True
            )
            
            if pull_res.returncode != 0:
                with open(log_file, "a") as f:
                    f.write(f"Pull failed: {pull_res.stderr}\n")
            else:
                with open(lock_file, "w") as f:
                    f.write("pulled\n")
                    
        except Exception as e:
            with open(log_file, "a") as f:
                f.write(f"Exception during pull: {str(e)}\n{traceback.format_exc()}\n")
            
    print("{}")
    
    sys.stdout.flush()
    os._exit(0)

if __name__ == "__main__":
    main()
