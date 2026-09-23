"""OpenClaw bridge: a read-mostly client for the Amanah Trader HTTP API.

This package contains **no LLM calls of any kind**. OpenClaw owns the model and the
OpenRouter key; the bridge owns facts, formatting, and the two-tap approval mechanism.
CLAUDE.md's rule is that no LLM may make, approve or bypass a decision -- here that is
structural rather than conventional, because the model and the decision path run in
different processes with different credentials:

    mcp_server.py  spawned by OpenClaw    Basic creds, NO operator key
    relay.py       spawned by the owner   Basic creds + the nginx operator key

Only the relay can reach /paper/execute. See backend/test_bridge_no_llm_in_path.py,
which enforces that nothing here imports a model client and nothing outside imports
this package.
"""
