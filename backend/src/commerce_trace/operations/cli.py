from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import Config
from ..persistence import connect_business

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def migrate(settings: Config) -> None:
    migration_paths = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    if not migration_paths:
        raise RuntimeError("no migrations found")
    with connect_business(project_path(settings.database_path)) as connection:
        for path in migration_paths:
            connection.executescript(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")
        connection.commit()


def generate_data(settings: Config, profile: str) -> dict[str, Any]:
    sys.path.insert(0, str(PROJECT_ROOT))
    from data_generator.generate import (  # type: ignore[import-not-found]
        COPY_COLUMNS,
        generate,
        load_config,
    )

    config = load_config(PROJECT_ROOT / "data_generator" / "scenarios.yaml", profile)
    generated = generate(config)
    truncate_order = [
        "inventory_snapshots",
        "refunds",
        "payments",
        "order_items",
        "orders",
        "products",
        "categories",
        "customers",
    ]
    with connect_business(project_path(settings.database_path)) as connection:
        for table in truncate_order:
            connection.execute(f"DELETE FROM {table}")
        for table in [
            "categories",
            "products",
            "customers",
            "orders",
            "order_items",
            "payments",
            "refunds",
            "inventory_snapshots",
        ]:
            columns = COPY_COLUMNS[table]
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"""
                INSERT INTO {table} ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                [tuple(_sqlite_value(value) for value in row) for row in generated.tables[table]],
            )
        connection.execute(
            """
            INSERT INTO dataset_metadata
              (singleton, data_version, seed, profile, result_hash, row_counts, generated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (singleton) DO UPDATE SET
              data_version = excluded.data_version,
              seed = excluded.seed,
              profile = excluded.profile,
              result_hash = excluded.result_hash,
              row_counts = excluded.row_counts,
              generated_at = excluded.generated_at
            """,
            (
                generated.metadata["version"],
                generated.metadata["seed"],
                profile,
                generated.result_hash,
                json.dumps(generated.metadata["row_counts"]),
                datetime.utcnow().isoformat(),
            ),
        )
        connection.commit()
    print(json.dumps(generated.metadata, ensure_ascii=False, indent=2))
    return dict(generated.metadata)


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def dataset_exists(settings: Config) -> bool:
    try:
        with connect_business(
            project_path(settings.database_path),
            read_only=True,
        ) as connection:
            row = connection.execute(
                "SELECT EXISTS (SELECT 1 FROM dataset_metadata)"
            ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="commerce-trace")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply SQLite migrations")
    generate_parser = commands.add_parser(
        "generate-data",
        help="Generate and load fixed-seed ecommerce data",
    )
    generate_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    init_parser = commands.add_parser("init", help="Initialize CommerceTrace")
    init_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    init_parser.add_argument("--no-data", action="store_true", help="Skip data generation")
    init_parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Generate data only when no initialized dataset exists",
    )
    return root


def main_sync() -> None:
    args = parser().parse_args()
    settings = Config()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "generate-data":
        migrate(settings)
        generate_data(settings, profile=args.profile)
    elif args.command == "init":
        migrate(settings)
        if args.no_data:
            print("Environment initialized without data.")
        elif args.if_empty and dataset_exists(settings):
            print("Environment already contains data; skipped data generation.")
        else:
            generate_data(settings, profile=args.profile)
            print("Environment initialized with data.")
