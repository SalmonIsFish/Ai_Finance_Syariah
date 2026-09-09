"""Regression tests for auth.py: the authentication + RBAC layer protecting
SC Malaysia publication mutations (approve/reject/activate/deactivate).

Phase 2A objective 1/2/4/8. Covers:
  - unauthenticated / unknown-user / wrong-password login attempts are
    rejected (AuthenticationError)
  - a correctly authenticated admin is permitted every action
  - a correctly authenticated reviewer is permitted approve/reject but
    rejected (AuthorizationError) for activate/deactivate
  - actor identity always comes from the credentials that authenticated,
    never from any other string a caller might supply -- there is no
    parameter anywhere in this module that accepts an identity directly,
    so "activated_by=fake-admin"-style spoofing has no code path to exploit
  - a missing/malformed SC_ADMIN_AUTH_USERS fails closed (rejects every
    login), never silently permits one
"""

import os

import auth


def _set_users(users_json: str) -> None:
    os.environ["SC_ADMIN_AUTH_USERS"] = users_json


def _user_entry(username: str, password: str, role: str) -> dict:
    return {"username": username, "password_hash": auth.hash_password(password), "role": role}


def test_unauthenticated_or_unknown_user_is_rejected():
    import json

    _set_users(json.dumps([_user_entry("alice", "correct-horse", "reviewer")]))
    try:
        auth.authenticate_credentials("mallory", "anything")
        raise AssertionError("unknown username must not authenticate")
    except auth.AuthenticationError:
        pass
    print("PASS: an unknown username is rejected with AuthenticationError")


def test_invalid_password_is_rejected():
    import json

    _set_users(json.dumps([_user_entry("alice", "correct-horse", "reviewer")]))
    try:
        auth.authenticate_credentials("alice", "wrong-password")
        raise AssertionError("wrong password must not authenticate")
    except auth.AuthenticationError:
        pass
    print("PASS: a wrong password is rejected with AuthenticationError")


def test_valid_admin_is_permitted_every_action():
    import json

    _set_users(json.dumps([_user_entry("bob", "hunter2", "admin")]))
    actor = auth.authenticate_credentials("bob", "hunter2")
    assert actor.username == "bob"
    assert actor.role == "admin"
    for action in ["approve", "reject", "activate", "deactivate"]:
        auth.authorize(actor, action)  # must not raise
    print("PASS: a valid admin is authorized for approve/reject/activate/deactivate")


def test_reviewer_is_rejected_for_activate_and_deactivate():
    import json

    _set_users(json.dumps([_user_entry("alice", "correct-horse", "reviewer")]))
    actor = auth.authenticate_credentials("alice", "correct-horse")
    auth.authorize(actor, "approve")  # allowed
    auth.authorize(actor, "reject")  # allowed
    for action in ["activate", "deactivate"]:
        try:
            auth.authorize(actor, action)
            raise AssertionError(f"reviewer must not be authorized for {action}")
        except auth.AuthorizationError:
            pass
    print("PASS: a reviewer is authorized for approve/reject but rejected for activate/deactivate")


def test_actor_identity_always_comes_from_authentication_not_a_free_parameter():
    """There is no function in this module that accepts an identity string
    directly -- authenticate_credentials always derives Actor.username from
    the username that presented valid credentials. Demonstrate that
    authenticating as one user can never produce another user's identity,
    no matter what that other username's own valid credentials are."""
    import json

    _set_users(
        json.dumps(
            [
                _user_entry("alice", "alice-pass", "reviewer"),
                _user_entry("bob", "bob-pass", "admin"),
            ]
        )
    )
    actor = auth.authenticate_credentials("alice", "alice-pass")
    assert actor.username == "alice", (
        "authenticating with alice's own credentials must always yield alice, never a "
        "different identity"
    )
    # Authenticating as alice with bob's password is simply a failed login --
    # there is no path from "I know bob's password" plus "I typed alice as
    # the username" to an Actor named alice with bob's role, or vice versa.
    try:
        auth.authenticate_credentials("alice", "bob-pass")
        raise AssertionError(
            "a username/password mismatch across two real users must not authenticate"
        )
    except auth.AuthenticationError:
        pass
    print(
        "PASS: authenticated actor identity always matches the credentials presented, "
        "never a spoofed username"
    )


def test_missing_or_malformed_auth_users_fails_closed():
    _set_users("")
    try:
        auth.authenticate_credentials("anyone", "anything")
        raise AssertionError("an empty SC_ADMIN_AUTH_USERS must reject every login")
    except auth.AuthenticationError:
        pass

    _set_users("{not valid json")
    try:
        auth.authenticate_credentials("anyone", "anything")
        raise AssertionError("a malformed SC_ADMIN_AUTH_USERS must reject every login")
    except auth.AuthenticationError:
        pass
    print(
        "PASS: missing or malformed SC_ADMIN_AUTH_USERS fails closed, never silently permits login"
    )


def main():
    test_unauthenticated_or_unknown_user_is_rejected()
    test_invalid_password_is_rejected()
    test_valid_admin_is_permitted_every_action()
    test_reviewer_is_rejected_for_activate_and_deactivate()
    test_actor_identity_always_comes_from_authentication_not_a_free_parameter()
    test_missing_or_malformed_auth_users_fails_closed()
    print("\nAll admin authentication/authorization regression tests passed.")


if __name__ == "__main__":
    main()
