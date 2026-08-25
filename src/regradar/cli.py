"""Local dev entrypoints: run a single filing through the pipeline, run eval suites, etc."""

import argparse
import asyncio

from regradar.core.db import get_session_factory


def _poll_once() -> None:
    """Run a single ingestion cycle across all active sources, then exit."""
    from regradar.ingestion.flows import poll_all_sources

    summary = asyncio.run(poll_all_sources())
    if not summary:
        print("No active sources configured — nothing polled.")
        return

    for source_name, count in summary.items():
        if count == -1:
            print(f"{source_name}: FAILED (see logs)")
        else:
            print(f"{source_name}: {count} new filing(s)")


def _create_api_key(*, owner_label: str, role: str) -> None:
    """Mint a new API key: hash it, insert the row, print the plaintext once.

    This is a bootstrap mechanism for local dev, tests, and live
    verification — no ticket has built a real key-issuance endpoint yet
    (FE-07 defers that to V2).
    """
    from regradar.core.api_keys import generate_api_key, hash_api_key
    from regradar.models.api_key import ApiKey
    from regradar.models.enums import ApiKeyRole

    try:
        role_enum = ApiKeyRole(role)
    except ValueError:
        valid = ", ".join(member.value for member in ApiKeyRole)
        raise SystemExit(f"Invalid role '{role}'. Must be one of: {valid}") from None

    plaintext_key = generate_api_key()

    async def _insert() -> None:
        session_factory = get_session_factory()
        async with session_factory() as db:
            key = ApiKey(
                key_hash=hash_api_key(plaintext_key),
                owner_label=owner_label,
                role=role_enum,
                is_active=True,
            )
            db.add(key)
            await db.commit()

    asyncio.run(_insert())

    print(f"Created API key for '{owner_label}' with role '{role_enum.value}'.")
    print(f"Key (shown once, will not be shown again): {plaintext_key}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="regradar")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "poll-once", help="Run a single ingestion cycle across all active sources, then exit."
    )
    create_key_parser = subparsers.add_parser(
        "create-api-key", help="Mint a new API key and print it once."
    )
    create_key_parser.add_argument("--owner-label", required=True)
    create_key_parser.add_argument("--role", required=True)

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    elif args.command == "create-api-key":
        _create_api_key(owner_label=args.owner_label, role=args.role)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
