"""Local dev entrypoints: run a single filing through the pipeline, run eval suites, etc."""

import argparse
import asyncio


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="regradar")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "poll-once", help="Run a single ingestion cycle across all active sources, then exit."
    )

    args = parser.parse_args()

    if args.command == "poll-once":
        _poll_once()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
