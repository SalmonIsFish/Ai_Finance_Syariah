"""The relay can actually be started, and says what it can do before it does it.

`relay.py` had no entrypoint at all: no `main()`, no `__main__` guard, and it read no
environment. Meanwhile `docs/deployment/openclaw-bridge.md` instructed the owner to set
`BRIDGE_TELEGRAM_TOKEN`, `BRIDGE_TELEGRAM_CHAT_ID` and `BRIDGE_TELEGRAM_OWNER_ID` -- three
names nothing in the codebase read. Documentation for a capability the code did not expose.

Two properties matter here beyond "it starts":

* **It fails closed with a message that names the fix.** A relay that starts half-configured
  and refuses at tap 2 is worse than one that refuses to start.
* **It announces whether it holds the operator key.** A relay that will only ever dry-run
  must not look identical to one that can submit to a broker.
"""

import pytest
from bridge import proposals, relay as relay_module
from bridge.relay import REQUIRED_ENV, build_relay, topics_from_env

BASE_ENV = {
    "BRIDGE_TELEGRAM_TOKEN": "123456:fake-token",
    "BRIDGE_TELEGRAM_CHAT_ID": "-100123456789",
    "BRIDGE_TELEGRAM_OWNER_ID": "42424242",
    "BRIDGE_BASIC_USER": "project_owner",
    "BRIDGE_BASIC_PASSWORD": "pw",
}


def test_it_refuses_to_start_without_the_telegram_settings():
    for missing in REQUIRED_ENV:
        env = {key: value for key, value in BASE_ENV.items() if key != missing}
        with pytest.raises(RuntimeError) as caught:
            build_relay(env, db_path=":memory:")
        message = str(caught.value)
        assert missing in message, f"the error must name {missing}"
        assert "backend/bridge/.env" in message, "and must name where to put it"


def test_an_empty_value_counts_as_missing():
    """A blank line in a .env file is not configuration."""
    env = dict(BASE_ENV, BRIDGE_TELEGRAM_TOKEN="   ")
    with pytest.raises(RuntimeError):
        build_relay(env, db_path=":memory:")


def test_it_refuses_to_start_without_the_basic_credentials():
    env = {key: value for key, value in BASE_ENV.items() if key != "BRIDGE_BASIC_PASSWORD"}
    with pytest.raises(RuntimeError):
        build_relay(env, db_path=":memory:")


def test_it_starts_without_an_operator_key_and_says_so():
    """The intended default: the whole flow is exercisable, and tap 2 dry-runs."""
    relay = build_relay(BASE_ENV, db_path=":memory:")
    assert relay.can_execute is False


def test_an_operator_key_is_picked_up_and_reported():
    relay = build_relay(dict(BASE_ENV, BRIDGE_OPERATOR_KEY="f" * 64), db_path=":memory:")
    assert relay.can_execute is True


def test_a_blank_operator_key_does_not_count_as_holding_one():
    """A .env line left as `BRIDGE_OPERATOR_KEY=` must not read as armed."""
    relay = build_relay(dict(BASE_ENV, BRIDGE_OPERATOR_KEY="  "), db_path=":memory:")
    assert relay.can_execute is False


def test_the_identity_checks_are_wired_from_the_environment():
    relay = build_relay(BASE_ENV, db_path=":memory:")
    allowed = {
        "from": {"id": 42424242},
        "message": {"chat": {"id": -100123456789}},
    }
    assert relay.is_authorised(allowed) is True
    assert relay.is_authorised({**allowed, "from": {"id": 999}}) is False
    assert relay.is_authorised({"from": {"id": 42424242}, "message": {"chat": {"id": -1}}}) is False


def test_forum_topics_are_optional_and_parsed_from_the_environment():
    topics = topics_from_env(
        {
            "BRIDGE_TELEGRAM_TOPIC_TRADES": "7",
            "BRIDGE_TELEGRAM_TOPIC_COMPLIANCE": " 12 ",
            "BRIDGE_TELEGRAM_TOPIC_BROKEN": "not-a-number",
            "UNRELATED": "3",
        }
    )
    assert topics == {"trades": 7, "compliance": 12}, (
        "a malformed topic id must be dropped, not crash the relay at startup"
    )
    assert topics_from_env({}) == {}


def test_the_proposal_store_is_opened_and_usable():
    relay = build_relay(BASE_ENV, db_path=":memory:")
    assert proposals.open_proposals(relay._db) == []


def test_main_reports_a_configuration_error_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(relay_module, "load_env_file", lambda *a, **k: None, raising=False)
    monkeypatch.delenv("BRIDGE_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("BRIDGE_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("BRIDGE_TELEGRAM_OWNER_ID", raising=False)

    code = relay_module.main(["--iterations", "0", "--db", ":memory:"])

    assert code == 1
    assert "configuration error" in capsys.readouterr().err


def test_main_announces_the_dry_run_state_at_startup(monkeypatch, capsys):
    """Said at startup rather than at tap 2, so the state is never a surprise."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("BRIDGE_OPERATOR_KEY", raising=False)

    code = relay_module.main(["--iterations", "0", "--db", ":memory:"])

    assert code == 0
    printed = capsys.readouterr().out
    assert "No operator key" in printed
    assert "dry run" in printed


def test_main_announces_when_it_can_actually_submit(monkeypatch, capsys):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BRIDGE_OPERATOR_KEY", "f" * 64)

    relay_module.main(["--iterations", "0", "--db", ":memory:"])

    printed = capsys.readouterr().out
    assert "WILL submit" in printed


def test_the_startup_line_never_prints_a_secret(monkeypatch, capsys):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("BRIDGE_OPERATOR_KEY", "f" * 64)

    relay_module.main(["--iterations", "0", "--db", ":memory:"])

    printed = capsys.readouterr().out
    assert "f" * 64 not in printed
    assert BASE_ENV["BRIDGE_BASIC_PASSWORD"] not in printed
    assert BASE_ENV["BRIDGE_TELEGRAM_TOKEN"] not in printed
