"""Smoke tests: the package installs cleanly and the ORM schema registers as expected."""


def test_package_imports() -> None:
    import regradar  # noqa: F401


def test_all_models_register_on_base_metadata() -> None:
    import regradar.models  # noqa: F401
    from regradar.core.db import Base

    expected_tables = {
        "api_keys",
        "briefs",
        "deliveries",
        "eval_runs",
        "extractions",
        "filing_chunks",
        "filings",
        "source_configs",
        "webhooks",
    }
    assert expected_tables == set(Base.metadata.tables.keys())


def test_cli_entrypoint_importable() -> None:
    from regradar.cli import main

    assert callable(main)
