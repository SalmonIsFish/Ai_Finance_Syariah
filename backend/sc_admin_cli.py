"""Human-review CLI for SC Malaysia publications.

This is the ONLY way to approve, activate, reject, or deactivate a
publication in this system. No HTTP mutation route exists for these
operations -- exposing them over HTTP would mean any caller reaching this
server could approve or activate a publication, exactly the kind of
unauthenticated-mutation surface the Phase 0 audit found and closed for
/paper/approval. A human runs this script directly on the machine that has
access to backend/paper_trading.db; it is never exposed as a route in
local_api.py. The read-only /api/universe/publications and
/api/shariah/publication/{id} endpoints remain safe to expose publicly,
since they cannot change anything.

Every mutating subcommand (approve/reject/activate/deactivate) REQUIRES
authentication -- see auth.py. There is no flag that accepts an identity
string directly: the actor recorded in approved_by/activated_by/
deactivated_by is always the username that authenticated, never
client-supplied text. Two roles exist: "reviewer" (approve, reject) and
"admin" (approve, reject, activate, deactivate) -- see auth.py's module
docstring for why activation is admin-only. Provide credentials with
--username plus either an interactive password prompt (default, not echoed,
not kept in shell history) or the SC_ADMIN_CLI_PASSWORD environment
variable for scripted/headless use (documented tradeoff: an env var is
less secure than an interactive prompt, since it can appear in process
listings or shell history depending on how it's set -- prefer the prompt
whenever a human is present).

Every mutating subcommand still defaults to a DRY RUN (prints what would
happen, changes nothing) and requires an explicit --apply flag to actually
write -- matching this repo's existing convention for dangerous CLI tools
(see provision_cash_account.py). Reconciliation passing never approves
anything by itself: approve is always a separate, explicit human action,
and activate is a further explicit action after that.

    python backend/sc_admin_cli.py list
    python backend/sc_admin_cli.py show sc-sac-my-2026-05-29
    python backend/sc_admin_cli.py securities sc-sac-my-2026-05-29 --ticker 1155
    python backend/sc_admin_cli.py approve sc-sac-my-2026-05-29 --username alice --notes "..." --apply
    python backend/sc_admin_cli.py activate sc-sac-my-2026-05-29 --username alice --apply
    python backend/sc_admin_cli.py deactivate sc-sac-my-2026-05-29 --username alice --reason "..." --apply
"""

import argparse
import getpass
import json
import os
import sqlite3

import auth
import sc_malaysia_store


def _connect() -> sqlite3.Connection:
    conn = sc_malaysia_store.connect_default()
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str, sort_keys=True))


def _authenticate(args: argparse.Namespace, *, action: str) -> auth.Actor | None:
    """Authenticates --username against SC_ADMIN_CLI_PASSWORD or an
    interactive prompt, then checks the resulting actor is authorized for
    `action`. Prints an explicit failure reason and returns None on any
    failure -- callers must treat None as "do not proceed", never fall back
    to an unauthenticated path.
    """
    password = os.environ.get("SC_ADMIN_CLI_PASSWORD")
    if password is None:
        try:
            password = getpass.getpass(f"Password for {args.username}: ")
        except (EOFError, KeyboardInterrupt):
            print("\nAuthentication cancelled.")
            return None
    try:
        actor = auth.authenticate_credentials(args.username, password)
    except auth.AuthenticationError:
        print(f"AUTHENTICATION FAILED for username '{args.username}'.")
        return None
    try:
        auth.authorize(actor, action)
    except auth.AuthorizationError as exc:
        print(f"AUTHORIZATION FAILED: {exc}")
        return None
    print(f"Authenticated as '{actor.username}' (role={actor.role}).")
    return actor


def cmd_list(args: argparse.Namespace) -> None:
    conn = _connect()
    pubs = sc_malaysia_store.list_publications(conn)
    if not pubs:
        print("(no publications ingested)")
        return
    for pub in pubs:
        active = "ACTIVE" if pub["activated_at"] and not pub["deactivated_at"] else ""
        print(
            f"{pub['id']:28} status={pub['human_review_status']:20} "
            f"official={pub['official_record_count']!s:>5} parsed={pub['parsed_record_count']!s:>5} {active}"
        )


def cmd_show(args: argparse.Namespace) -> None:
    conn = _connect()
    pub = sc_malaysia_store.get_publication(conn, args.publication_id)
    if pub is None:
        print(f"Publication not found: {args.publication_id}")
        raise SystemExit(1)
    securities = sc_malaysia_store.publication_securities(conn, args.publication_id)
    compliant = sum(1 for s in securities if s["shariah_status"] == "COMPLIANT")
    _print(dict(pub))
    print()
    print(
        f"Security records: {len(securities)} ({compliant} COMPLIANT, {len(securities) - compliant} other)"
    )
    print()
    print("Reconciliation summary:")
    print(f"  official_record_count:     {pub['official_record_count']}")
    print(f"  extractable_record_count:  {pub['extractable_record_count']}")
    print(f"  parsed_record_count:       {pub['parsed_record_count']}")
    reconciled = (
        pub["official_record_count"] is not None
        and pub["official_record_count"]
        == pub["extractable_record_count"]
        == pub["parsed_record_count"]
    )
    print(f"  reconciles:                {reconciled}")
    print(f"  source_document_hash:      {pub['source_document_hash']}")
    print(f"  parser_version:            {pub['parser_version']}")


def cmd_securities(args: argparse.Namespace) -> None:
    conn = _connect()
    securities = sc_malaysia_store.publication_securities(conn, args.publication_id)
    if not securities:
        print(f"No security records for publication {args.publication_id} (does it exist?)")
        raise SystemExit(1)
    if args.ticker:
        securities = [s for s in securities if s["ticker"] == args.ticker]
        if not securities:
            print(f"Ticker {args.ticker} not found in publication {args.publication_id}")
            raise SystemExit(1)
    for sec in securities:
        _print(dict(sec))


def cmd_approve(args: argparse.Namespace) -> None:
    actor = _authenticate(args, action="approve")
    if actor is None:
        raise SystemExit(1)
    conn = _connect()
    pub = sc_malaysia_store.get_publication(conn, args.publication_id)
    if pub is None:
        print(f"Publication not found: {args.publication_id}")
        raise SystemExit(1)
    print(f"Publication:      {args.publication_id}")
    print(f"Current status:   {pub['human_review_status']}")
    print(
        f"Record counts:    official={pub['official_record_count']} "
        f"extractable={pub['extractable_record_count']} parsed={pub['parsed_record_count']}"
    )
    print(f"Reviewer:         {actor.username} (authenticated, role={actor.role})")
    print(f"Notes:            {args.notes or '(none)'}")
    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to actually approve.")
        return
    result = sc_malaysia_store.approve_publication(
        conn, args.publication_id, notes=args.notes, reviewer=actor.username
    )
    _print(result)


def cmd_reject(args: argparse.Namespace) -> None:
    actor = _authenticate(args, action="reject")
    if actor is None:
        raise SystemExit(1)
    conn = _connect()
    pub = sc_malaysia_store.get_publication(conn, args.publication_id)
    if pub is None:
        print(f"Publication not found: {args.publication_id}")
        raise SystemExit(1)
    print(f"Publication:      {args.publication_id}")
    print(f"Current status:   {pub['human_review_status']}")
    print(f"Reason:           {args.reason}")
    print(f"Reviewer:         {actor.username} (authenticated, role={actor.role})")
    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to actually reject.")
        return
    result = sc_malaysia_store.reject_publication(
        conn, args.publication_id, reason=args.reason, reviewer=actor.username
    )
    _print(result)


def cmd_activate(args: argparse.Namespace) -> None:
    actor = _authenticate(args, action="activate")
    if actor is None:
        raise SystemExit(1)
    conn = _connect()
    pub = sc_malaysia_store.get_publication(conn, args.publication_id)
    if pub is None:
        print(f"Publication not found: {args.publication_id}")
        raise SystemExit(1)
    active = sc_malaysia_store.get_active_publication(conn)
    print(f"Publication:      {args.publication_id}")
    print(f"Current status:   {pub['human_review_status']}")
    print(f"Approved by/at:   {pub.get('approved_by')} / {pub.get('approved_at')}")
    if active:
        note = " (this one)" if active["id"] == args.publication_id else " -- WILL BE DEACTIVATED"
        print(f"Currently active: {active['id']}{note}")
    else:
        print("Currently active: (none)")
    print(f"Activating as:    {actor.username} (authenticated, role={actor.role})")
    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to actually activate.")
        print("Activation safety checks (approved, not rejected, not needs_reconciliation, has")
        print("security records, record counts reconcile, source hash present, parser version")
        print("present, not already superseded) are enforced by activate_publication itself when")
        print("you re-run with --apply -- if any fail, nothing is written and a reason is printed.")
        return
    result = sc_malaysia_store.activate_publication(
        conn, args.publication_id, activated_by=actor.username
    )
    _print(result)


def cmd_deactivate(args: argparse.Namespace) -> None:
    actor = _authenticate(args, action="deactivate")
    if actor is None:
        raise SystemExit(1)
    conn = _connect()
    pub = sc_malaysia_store.get_publication(conn, args.publication_id)
    if pub is None:
        print(f"Publication not found: {args.publication_id}")
        raise SystemExit(1)
    print(f"Publication:      {args.publication_id}")
    print(f"Currently active: {bool(pub['activated_at']) and not pub['deactivated_at']}")
    print(f"Reason:           {args.reason}")
    print(f"Deactivating as:  {actor.username} (authenticated, role={actor.role})")
    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply to actually deactivate.")
        print("Trading falls back to UNKNOWN (fail closed) immediately after this -- no")
        print("replacement publication is activated automatically.")
        return
    result = sc_malaysia_store.deactivate_publication(
        conn, args.publication_id, reason=args.reason, deactivated_by=actor.username
    )
    _print(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all SC publications").set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one publication's metadata + reconciliation summary")
    p_show.add_argument("publication_id")
    p_show.set_defaults(func=cmd_show)

    p_sec = sub.add_parser("securities", help="List (or inspect one) security in a publication")
    p_sec.add_argument("publication_id")
    p_sec.add_argument("--ticker", default=None)
    p_sec.set_defaults(func=cmd_securities)

    p_approve = sub.add_parser("approve", help="Approve a publication (dry run unless --apply)")
    p_approve.add_argument("publication_id")
    p_approve.add_argument(
        "--username", required=True, help="Authenticated reviewer/admin username"
    )
    p_approve.add_argument("--notes", default=None)
    p_approve.add_argument("--apply", action="store_true")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="Reject a publication (dry run unless --apply)")
    p_reject.add_argument("publication_id")
    p_reject.add_argument("--reason", required=True)
    p_reject.add_argument("--username", required=True, help="Authenticated reviewer/admin username")
    p_reject.add_argument("--apply", action="store_true")
    p_reject.set_defaults(func=cmd_reject)

    p_activate = sub.add_parser(
        "activate", help="Activate an approved publication (dry run unless --apply)"
    )
    p_activate.add_argument("publication_id")
    p_activate.add_argument("--username", required=True, help="Authenticated admin username")
    p_activate.add_argument("--apply", action="store_true")
    p_activate.set_defaults(func=cmd_activate)

    p_deactivate = sub.add_parser(
        "deactivate", help="Deactivate the currently active publication (dry run unless --apply)"
    )
    p_deactivate.add_argument("publication_id")
    p_deactivate.add_argument("--reason", required=True)
    p_deactivate.add_argument("--username", required=True, help="Authenticated admin username")
    p_deactivate.add_argument("--apply", action="store_true")
    p_deactivate.set_defaults(func=cmd_deactivate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
