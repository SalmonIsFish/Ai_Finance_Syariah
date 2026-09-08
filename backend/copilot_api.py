"""AI Copilot for the Shariah screening dashboard.

This module provides a read-only, explanatory AI layer. It NEVER computes
Shariah status. It ONLY explains the deterministic status returned by
screening_api.py.

Invariants enforced:
1. authoritative_shariah_status comes strictly from screening_api.
2. The LLM must return a status_check that EXACTLY matches the authoritative
   status. If it disagrees or hallucinates, the request FAILS CLOSED.
3. The LLM response is grounded in actual evidence and vault data.
4. No mutations are possible from this module.
"""

import json
from datetime import datetime, timezone

import screening_api
from config import load_settings
from news_summarizer import openrouter_request


SYSTEM_PROMPT = """You are an explanatory AI Copilot for a Malaysian Shariah-compliant paper-trading dashboard.
You are a NEUTRAL EXPLAINER, not an analyst, not an adviser, and NOT a Shariah authority.

Your ONLY job is to explain the raw data provided to you (Shariah status, quant signals, evidence, and vault context) in a clear, institutional tone.

CRITICAL RULES:
1. The Shariah status of this ticker has ALREADY been definitively decided by the Securities Commission Malaysia and computed by this application's deterministic backend.
2. You MUST NOT recompute, question, or contradict the provided authoritative status.
3. You MUST NOT offer your own compliance opinion or risk advice.
4. You MUST NOT recommend buying or selling.
5. You MUST ground every claim in the provided data.

You MUST respond in valid JSON matching this schema exactly:
{
  "explanation": "A 2-3 paragraph explanation of the data, the status, and the quant signal.",
  "status_check": "MUST EXACTLY MATCH the provided authoritative_shariah_status. Options: PASS, REJECT, UNKNOWN"
}
"""

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def explain_ticker(ticker: str, question: str) -> dict:
    """Provides a fail-closed LLM explanation of the ticker's status.

    Raises ValueError if safety constraints fail.
    """
    settings = load_settings()
    if not settings.openrouter_api_key:
        raise ValueError("AI Copilot is not configured (openrouter_api_key missing).")

    normalized = ticker.strip().upper()

    # 1. Gather all strictly deterministic data
    screen_data = screening_api.screen_ticker(normalized)
    authoritative_status = screen_data["shariah"]["status"]  # PASS, REJECT, or UNKNOWN

    evidence_data = screening_api.evidence_for_ticker(normalized, limit=10)
    
    knowledge_context = {}
    if settings.shariah_wiki_path:
        knowledge_context = screening_api.knowledge_search(settings.shariah_wiki_path, normalized, limit=3)

    # 2. Construct the strict prompt
    user_content = json.dumps({
        "ticker": normalized,
        "authoritative_shariah_status": authoritative_status,
        "screen_data": screen_data,
        "evidence_log": evidence_data.get("items", []),
        "vault_context": knowledge_context.get("results", []),
        "user_question": question
    }, default=str)

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": 800,
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    # 3. Call OpenRouter
    response = openrouter_request(payload, api_key=settings.openrouter_api_key)
    if not isinstance(response, dict) or not response.get("ok"):
        raise ValueError(f"LLM request failed: {response.get('reason', 'unknown_error')}")

    data = response.get("data")
    if not isinstance(data, dict):
        raise ValueError("Malformed LLM response wrapper.")
        
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Empty choices in LLM response.")
        
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("Malformed message in LLM response.")
        
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Empty content in LLM response.")

    # 4. Parse the LLM's JSON and enforce invariants
    try:
        parsed_llm = json.loads(text.strip())
    except json.JSONDecodeError:
        raise ValueError("LLM failed to produce valid JSON.")

    llm_status_check = parsed_llm.get("status_check")
    explanation = parsed_llm.get("explanation")

    if not explanation:
        raise ValueError("LLM missing 'explanation' field.")

    if llm_status_check != authoritative_status:
        # INVARIANT 2: Fail closed on mismatch
        raise ValueError(
            f"SAFETY VIOLATION: LLM status_check '{llm_status_check}' "
            f"contradicts authoritative status '{authoritative_status}'."
        )

    # 5. Return composite safely
    return {
        "ticker": normalized,
        "authoritative_status": authoritative_status,
        "explanation": explanation,
        "model": settings.openrouter_model,
        "generated_at": _utc_now(),
        "sources_used": {
            "evidence_count": len(evidence_data.get("items", [])),
            "vault_count": len(knowledge_context.get("results", []))
        }
    }
