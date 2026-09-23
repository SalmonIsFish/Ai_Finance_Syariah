"""No secret reaches a log, a rendered block, a tool schema or the repository.

Four things must never escape:

* the `project_owner` password,
* the nginx operator key,
* the Telegram bot token,
* and `EXECUTE PAPER` -- which is not a secret in the cryptographic sense but is the one
  string that, combined with credentials, submits an order. Keeping it out of every
  surface a model can see means a model has never encountered it in a context where
  emitting it would do anything.

A repr, a traceback and an MCP error payload are all places a careless dataclass leaks a
password, so `BridgeConfig.__repr__` is checked directly rather than assumed.
"""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
BRIDGE = BACKEND / "bridge"
REPO = BACKEND.parent

PASSWORD = "correct-horse-battery-staple"
OPERATOR_KEY = "f0e1d2c3" * 8
TOKEN = "1234567890:AAH-fake-telegram-bot-token"

# Modules that legitimately contain the phrase: the one that sends it, and the tests that
# assert it is not anywhere else.
PHRASE_ALLOWED = {"relay.py"}


def _bridge_files():
    return [p for p in sorted(BRIDGE.rglob("*.py")) if "__pycache__" not in p.parts]


def check_the_config_repr_hides_every_secret() -> None:
    from bridge.client import BridgeConfig

    config = BridgeConfig(
        base_url="https://example.invalid",
        basic_user="project_owner",
        basic_password=PASSWORD,
        operator_key=OPERATOR_KEY,
    )
    for rendering in (repr(config), str(config), f"{config}", "{}".format(config)):
        assert PASSWORD not in rendering, "the password leaked through a config rendering"
        assert OPERATOR_KEY not in rendering, "the operator key leaked through a config rendering"
    assert "<redacted>" in repr(config)

    # And an exception carrying the config must not print it either.
    try:
        raise RuntimeError(f"failed with {config}")
    except RuntimeError as exc:
        assert PASSWORD not in str(exc)
        assert OPERATOR_KEY not in str(exc)


def check_the_basic_header_is_not_reconstructable_from_a_repr() -> None:
    """base64 is not encryption; a leaked Authorization header is a leaked password."""
    import base64

    from bridge.client import BridgeConfig

    config = BridgeConfig(
        base_url="https://example.invalid",
        basic_user="project_owner",
        basic_password=PASSWORD,
    )
    encoded = base64.b64encode(f"project_owner:{PASSWORD}".encode()).decode()
    assert encoded not in repr(config)


def check_rendered_blocks_never_carry_a_secret() -> None:
    """Whatever a bot shows the owner also goes through Telegram's servers."""
    from bridge import format as renderer

    facts = {
        "ticker": "4197",
        "verdict": {"status": "PASS", "source_document_hash": "abc123"},
        "password": PASSWORD,
        "operator_key": OPERATOR_KEY,
    }
    rendered = renderer.render_shariah(facts)
    assert PASSWORD not in rendered
    assert OPERATOR_KEY not in rendered


def check_no_secret_is_written_to_the_proposal_store() -> None:
    """Bridge state is a plain file on a laptop; it must not become a credential store."""
    from bridge import proposals

    connection = proposals.connect(":memory:")
    try:
        preview = {
            "status": "READY_FOR_APPROVAL",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 1,
            "price": 207.6,
            "asset_class": "equity",
        }
        record = proposals.create_proposal(connection, preview=preview, chat_id=1, market="US")
        proposals.mark_queued(
            connection, record["proposal_id"], approval={"queue_id": 1, "approval": {}}
        )
        dumped = "\n".join(connection.iterdump())
        for secret in (PASSWORD, OPERATOR_KEY, TOKEN):
            assert secret not in dumped
    finally:
        connection.close()


def check_the_confirmation_phrase_is_only_where_it_must_be() -> None:
    """A model must never see the phrase in a context where emitting it would act."""
    from bridge.relay import EXECUTE_PHRASE
    from bridge.roster import HOUSE_RULES, ROSTER, instructions_for
    from bridge.tools import TOOLS

    offenders = []
    for path in _bridge_files():
        if path.name in PHRASE_ALLOWED:
            continue
        if EXECUTE_PHRASE in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(BACKEND).as_posix())
    assert not offenders, f"the confirmation phrase appears outside relay.py: {offenders}"

    # Not in any tool description or schema the model is handed.
    for tool in TOOLS.values():
        assert EXECUTE_PHRASE not in tool.description, tool.name
        assert EXECUTE_PHRASE not in str(tool.params), tool.name

    # Not in any role text.
    for rule in HOUSE_RULES:
        assert EXECUTE_PHRASE not in rule
    for role in ROSTER:
        assert EXECUTE_PHRASE not in instructions_for(role), role


def check_the_operator_key_is_never_a_tool_argument() -> None:
    """It is attached by the HTTP client, so it never passes through model-visible data."""
    from bridge.tools import TOOLS

    for tool in TOOLS.values():
        keys = {str(key).lower() for key in tool.params}
        for forbidden in ("operator", "key", "token", "password", "secret", "auth"):
            assert not any(forbidden in key for key in keys), (tool.name, keys)


def check_no_secret_literal_is_committed() -> None:
    """A 64-hex string or a Telegram-token shape in source is a leak by another name."""
    operator_shape = re.compile(r"\b[0-9a-f]{64}\b")
    token_shape = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")
    offenders = []
    for path in _bridge_files():
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if operator_shape.search(node.value) or token_shape.search(node.value):
                    offenders.append(
                        f"{path.relative_to(BACKEND).as_posix()}: {node.value[:12]}..."
                    )
    assert not offenders, f"a credential-shaped literal is committed: {offenders}"


def check_gitignore_covers_the_bridge_secrets_and_state() -> None:
    """backend/.env and backend/*.sqlite3 do not match a subdirectory."""
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("backend/bridge/.env", "backend/bridge/state.sqlite3"):
        assert pattern in ignored, f"{pattern} is not gitignored"


def check_the_env_file_was_never_committed() -> None:
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "backend/bridge/.env", "backend/bridge/state.sqlite3"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", f"a bridge secret is tracked by git: {result.stdout}"


def main() -> None:
    check_the_config_repr_hides_every_secret()
    check_the_basic_header_is_not_reconstructable_from_a_repr()
    check_rendered_blocks_never_carry_a_secret()
    check_no_secret_is_written_to_the_proposal_store()
    check_the_confirmation_phrase_is_only_where_it_must_be()
    check_the_operator_key_is_never_a_tool_argument()
    check_no_secret_literal_is_committed()
    check_gitignore_covers_the_bridge_secrets_and_state()
    check_the_env_file_was_never_committed()
    print("PASS: no credential, and no confirmation phrase, escapes the bridge.")


if __name__ == "__main__":
    main()
