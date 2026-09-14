import sys
import subprocess
import json
import os
import traceback
import threading

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
        # timed out
        return ""
    return result["raw"]

def main():
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    agents_dir = os.path.dirname(scripts_dir)
    repo_root = os.path.dirname(agents_dir)
    github2_dir = os.path.dirname(repo_root)
    vault_dir = os.path.join(github2_dir, "amanah-trader-vault")
    
    log_file = os.path.join(github2_dir, "vault_sync_error.log")
    
    # Clean up this session's lock file if possible
    try:
        raw = read_stdin_with_timeout(1.0)
        if raw.strip():
            payload = json.loads(raw)
            conv_id = payload.get("session_id")
            if conv_id:
                lock_file = os.path.join(agents_dir, f".pull_lock_{conv_id}")
                if os.path.exists(lock_file):
                    os.remove(lock_file)
    except Exception:
        pass # Ignore errors in lock cleanup
    
    try:
        # 1. Add all
        subprocess.run(["git", "-C", vault_dir, "add", "-A"], check=True, capture_output=True)
        
        # 2. Commit
        res = subprocess.run(
            ["git", "-C", vault_dir, "commit", "-m", "Auto-sync vault from agent session"],
            capture_output=True
        )
        
        # 3. Push
        push_res = subprocess.run(["git", "-C", vault_dir, "push"], capture_output=True, text=True)
        
        if push_res.returncode != 0:
            if "Updates were rejected" in push_res.stderr or "fetch first" in push_res.stderr:
                with open(log_file, "a") as f:
                    f.write(f"Push failed (remote diverged): {push_res.stderr}\n")
            elif "Everything up-to-date" not in push_res.stderr and push_res.stderr.strip() != "":
                with open(log_file, "a") as f:
                    f.write(f"Push failed with error: {push_res.stderr}\n")
                    
    except Exception as e:
        with open(log_file, "a") as f:
            f.write(f"Exception during sync: {str(e)}\n{traceback.format_exc()}\n")
        
    # Output expected JSON for Stop hook
    print(json.dumps({"decision": "stop"}))
    
    # Force exit to prevent hanging on Windows OS I/O locks if stdin is held open by caller
    sys.stdout.flush()
    os._exit(0)

if __name__ == "__main__":
    main()
