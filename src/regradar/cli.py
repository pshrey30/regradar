"""Local dev entrypoints: run a single filing through the pipeline, run eval suites, etc."""

import argparse
import asyncio

from regradar.core.db import get_session_factory, set_rls_context

_DEFAULT_RATE_LIMIT_PER_MINUTE = 60  # matches ApiKey.rate_limit_per_minute's DB default (FOUND-02)


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


def _create_api_key(
    *, owner_label: str, role: str, rate_limit_per_minute: int | None = None
) -> None:
    """Mint a new API key: hash it, insert the row, print the plaintext once.

    This is a bootstrap mechanism for local dev, tests, and live
    verification — no ticket has built a real key-issuance endpoint yet
    (FE-07 defers that to V2). rate_limit_per_minute is optional and mainly
    useful for API-03's live verification, where a low limit (e.g. 3) lets a
    real 429 be triggered in a handful of requests instead of 60+.
    """
    from sqlalchemy import select

    from regradar.core.api_keys import generate_api_key, hash_api_key
    from regradar.models.api_key import ApiKey
    from regradar.models.enums import ApiKeyRole
    from regradar.models.organization import Organization

    try:
        role_enum = ApiKeyRole(role)
    except ValueError:
        valid = ", ".join(member.value for member in ApiKeyRole)
        raise SystemExit(f"Invalid role '{role}'. Must be one of: {valid}") from None

    plaintext_key = generate_api_key()
    resolved_rate_limit = (
        rate_limit_per_minute if rate_limit_per_minute is not None else _DEFAULT_RATE_LIMIT_PER_MINUTE
    )

    async def _insert() -> None:
        session_factory = get_session_factory()
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            # SEC-05: this CLI has no organization-management surface by
            # design (out of scope for this ticket) — new keys always
            # belong to the single, first-created organization.
            org_id = (
                await db.execute(select(Organization.id).order_by(Organization.created_at.asc()).limit(1))
            ).scalar_one()
            key = ApiKey(
                organization_id=org_id,
                key_hash=hash_api_key(plaintext_key),
                owner_label=owner_label,
                role=role_enum,
                is_active=True,
                rate_limit_per_minute=resolved_rate_limit,
            )
            db.add(key)
            await db.commit()

    asyncio.run(_insert())

    print(f"Created API key for '{owner_label}' with role '{role_enum.value}'.")
    print(f"Rate limit: {resolved_rate_limit} requests/minute.")
    print(f"Key (shown once, will not be shown again): {plaintext_key}")


def _run_eval(*, run_type: str) -> None:
    """Run EVAL-01's Ragas harness once and print a summary."""
    from regradar.evaluation.harness import run_eval
    from regradar.models.enums import EvalRunType

    run_type_enum = EvalRunType(run_type)
    run = asyncio.run(run_eval(run_type_enum))

    print(f"Eval run {run.id} ({run.run_type.value}): {'PASSED' if run.passed else 'FAILED'}")
    print(f"  ragas_faithfulness:   {run.ragas_faithfulness:.3f}")
    print(f"  ragas_context_recall: {run.ragas_context_recall:.3f}")


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
    create_key_parser.add_argument(
        "--rate-limit-per-minute",
        type=int,
        default=None,
        help="Override the key's rate limit (default: 60/minute).",
    )

    run_eval_parser = subparsers.add_parser(
        "run-eval", help="Run EVAL-01's Ragas harness once and print a summary."
    )
    run_eval_parser.add_argument(
        "--run-type",
        choices=["manual", "scheduled", "pre_deploy_regression"],
        default="manual",
    )

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    elif args.command == "create-api-key":
        _create_api_key(
            owner_label=args.owner_label,
            role=args.role,
            rate_limit_per_minute=args.rate_limit_per_minute,
        )
    elif args.command == "run-eval":
        _run_eval(run_type=args.run_type)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
