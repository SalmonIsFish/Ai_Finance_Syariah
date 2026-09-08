"""Minimal server-side authentication + role-based authorization for SC
Malaysia publication governance mutations (approve/reject/activate/deactivate).

Design intentionally small for this single/small-team operator system, per
the Phase 2A instruction to prefer the smallest production-sensible
mechanism over a large auth framework:

  - Credentials (username + salted password hash + role) are configured via
    the SC_ADMIN_AUTH_USERS environment variable: a JSON array of
    {"username": ..., "password_hash": ..., "role": "admin"|"reviewer"}.
    Real credentials must live in backend/.env (gitignored), never in this
    file or any committed file. See .env.example for the exact format and
    `python auth.py hash <password>` to generate a hash to put there.
  - Password hashes use PBKDF2-HMAC-SHA256 (stdlib hashlib -- no extra
    dependency), stored as "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>".
  - Two roles: "reviewer" (approve, reject) and "admin" (approve, reject,
    activate, deactivate). No separate "activator" role exists: activation
    is the single highest-stakes action in this system -- it is the one
    thing that can make a publication authoritative for a live Shariah PASS
    -- so it is reserved for admin only, alongside deactivation. A
    three-role split (reviewer / approver / activator) was considered and
    rejected as unnecessary complexity for the current single/small-team
    operator model; if the team grows and duties around activation
    specifically need to be separated from general admin, extend
    ROLE_PERMISSIONS below without touching the credential mechanism.
  - No session/token store exists yet because no HTTP mutation route exists
    yet (Phase 2A keeps the CLI as the only mutation surface; see
    sc_admin_cli.py). authenticate_credentials() is the same function an
    HTTP login route would call in a future phase; issuing and verifying a
    session token around it is a thin addition, deliberately not built
    until an HTTP mutation surface actually needs it.

This module never trusts a client-supplied identity string. The only way to
become a given Actor is to present a username and password that verify
against SC_ADMIN_AUTH_USERS; the returned Actor.username is always the
username that authenticated, never something read back out of a request
payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass

import config  # noqa: F401  (import ensures backend/.env has been loaded into os.environ)

PBKDF2_ITERATIONS = 260_000

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "reviewer": {"approve", "reject", "propose"},
    "admin": {"approve", "reject", "activate", "deactivate", "propose", "execute"},
}

# Used only to burn comparable CPU time for an unknown username, so login
# failures for "no such user" and "wrong password" take the same time and
# do not leak which one occurred. Never a valid credential.
_DUMMY_HASH = "pbkdf2_sha256$1000$00$00"


@dataclass(frozen=True)
class Actor:
    """An authenticated identity. Only produced by authenticate_credentials."""

    username: str
    role: str

    def can(self, action: str) -> bool:
        return action in ROLE_PERMISSIONS.get(self.role, set())


class AuthenticationError(Exception):
    """Raised when credentials do not authenticate to a known actor."""


class AuthorizationError(Exception):
    """Raised when an authenticated actor lacks permission for an action."""


def hash_password(plain_password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(plain_password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_hex, hash_hex = encoded_hash.split("$")
    except (ValueError, AttributeError):
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _load_configured_users() -> dict[str, dict]:
    """Parses SC_ADMIN_AUTH_USERS. Fails closed (empty dict, so every login
    attempt is rejected) on a missing or malformed value rather than raising
    or silently granting access."""
    raw = os.environ.get("SC_ADMIN_AUTH_USERS", "").strip()
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(entries, list):
        return {}
    users: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username = str(entry.get("username", "")).strip()
        password_hash = entry.get("password_hash")
        role = entry.get("role")
        if not username or not password_hash or role not in ROLE_PERMISSIONS:
            continue
        users[username] = {"password_hash": password_hash, "role": role}
    return users


def authenticate_credentials(username: str, password: str) -> Actor:
    """Verify a username/password pair against SC_ADMIN_AUTH_USERS.

    Raises AuthenticationError on any failure. An unknown username and a
    known username with the wrong password are indistinguishable to the
    caller -- both raise the same error, and a dummy hash comparison always
    runs so the two cases are not distinguishable by response timing either.
    Fails closed: an empty or unparseable SC_ADMIN_AUTH_USERS means every
    login attempt is rejected, never silently permitted.
    """
    users = _load_configured_users()
    normalized_username = str(username or "").strip()
    record = users.get(normalized_username)
    encoded_hash = record["password_hash"] if record else _DUMMY_HASH
    valid = verify_password(password or "", encoded_hash)
    if not record or not valid:
        raise AuthenticationError("invalid_credentials")
    return Actor(username=normalized_username, role=record["role"])


def authorize(actor: Actor, action: str) -> None:
    """Raises AuthorizationError if `actor`'s role cannot perform `action`."""
    if not actor.can(action):
        raise AuthorizationError(f"role '{actor.role}' cannot perform '{action}'")


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
    else:
        print("Usage: python auth.py hash <password>")
        print("Prints a pbkdf2_sha256$... hash to paste into SC_ADMIN_AUTH_USERS in backend/.env")
        sys.exit(1)
