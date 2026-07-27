from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..config import Settings
from ..persistence import connect_sqlite
from ..runtime import FeatureConfiguration, build_runtime
from .evaluation import (
    EvaluationReport,
    load_dataset,
    run_evaluation,
    write_ablation_report,
    write_report,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def migrate(settings: Settings) -> None:
    """按文件名顺序应用项目中的全部 SQLite 迁移脚本。"""

    migration_paths = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    if not migration_paths:
        raise RuntimeError("no migrations found")
    with connect_sqlite(project_path(settings.database_path)) as connection:
        for path in migration_paths:
            connection.executescript(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")
        connection.commit()


def generate_data(settings: Settings, profile: str) -> dict[str, Any]:
    """按指定场景生成固定种子数据并装载到业务数据库。"""

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
    with connect_sqlite(project_path(settings.database_path)) as connection:
        for table in truncate_order:
            connection.execute(f"DELETE FROM ecommerce.{table}")
        insert_order = [
            "categories",
            "products",
            "customers",
            "orders",
            "order_items",
            "payments",
            "refunds",
            "inventory_snapshots",
        ]
        for table in insert_order:
            columns = COPY_COLUMNS[table]
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"""
                INSERT INTO ecommerce.{table} ({", ".join(columns)})
                VALUES ({placeholders})
                """,
                [tuple(_sqlite_value(value) for value in row) for row in generated.tables[table]],
            )
        connection.execute(
            """
            INSERT INTO agent_app.dataset_metadata
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
    """把生成器值转换为 SQLite 可直接绑定的基础类型。"""

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def dataset_exists(settings: Settings) -> bool:
    """检查数据库中是否存在已初始化的数据集元数据。"""

    try:
        with connect_sqlite(project_path(settings.database_path), read_only=True) as connection:
            row = connection.execute(
                "SELECT EXISTS (SELECT 1 FROM agent_app.dataset_metadata)"
            ).fetchone()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def project_path(path: Path) -> Path:
    """将相对路径解析为相对于后端项目根目录的路径。"""

    return path if path.is_absolute() else PROJECT_ROOT / path


async def evaluate(settings: Settings, *, limit: int | None) -> None:
    """构建完整运行时并执行一次可复现评估。"""

    runtime = build_runtime(settings)
    for resource in runtime.resources:
        await resource.open()
    try:
        dataset = load_dataset(project_path(settings.eval_dataset_path))
        report = await run_evaluation(
            agent=runtime.agent,
            dataset=dataset,
            configuration={
                "model_provider": "deepseek",
                "model": settings.deepseek_model,
                "schema_version": settings.schema_version,
                "knowledge_version": settings.knowledge_version,
                "data_seed": 20260725,
            },
            limit=limit,
        )
        paths = write_report(report, PROJECT_ROOT / "reports")
        print("\n".join(str(path) for path in paths))
    finally:
        for resource in reversed(runtime.resources):
            await resource.close()


async def ablation(settings: Settings, *, limit: int | None) -> None:
    """依次运行不同功能组合并生成消融对比报告。"""

    variants = {
        "A_schema_only": FeatureConfiguration(
            include_knowledge=False,
            enable_sql_retries=False,
        ),
        "B_with_business_rules": FeatureConfiguration(
            enable_sql_retries=False,
        ),
        "C_with_sql_retries": FeatureConfiguration(),
    }
    dataset = load_dataset(project_path(settings.eval_dataset_path))
    runs: dict[str, EvaluationReport] = {}
    for name, features in variants.items():
        runtime = build_runtime(settings, features)
        for resource in runtime.resources:
            await resource.open()
        try:
            runs[name] = await run_evaluation(
                agent=runtime.agent,
                dataset=dataset,
                configuration={
                    "variant": name,
                    "features": asdict(features),
                    "model_provider": "deepseek",
                    "model": settings.deepseek_model,
                    "schema_version": settings.schema_version,
                    "knowledge_version": settings.knowledge_version,
                    "data_seed": 20260725,
                },
                limit=limit,
            )
        finally:
            for resource in reversed(runtime.resources):
                await resource.close()
    paths = write_ablation_report(
        runs=runs,
        configuration={
            "dataset": dataset.version,
            "limit": limit,
            "model_provider": "deepseek",
            "model": settings.deepseek_model,
            "data_seed": 20260725,
        },
        directory=PROJECT_ROOT / "reports",
    )
    print("\n".join(str(path) for path in paths))


def parser() -> argparse.ArgumentParser:
    """构建迁移、数据生成、评估和初始化命令的参数解析器。"""

    root = argparse.ArgumentParser(prog="commerce-trace")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply SQLite migrations")
    generate_parser = commands.add_parser(
        "generate-data", help="Generate and load fixed-seed ecommerce data"
    )
    generate_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    evaluate_parser = commands.add_parser("evaluate", help="Run the reproducible MVP evaluation")
    evaluate_parser.add_argument("--limit", type=int, default=None)
    ablation_parser = commands.add_parser(
        "ablation", help="Run reproducible A-D architecture ablations"
    )
    ablation_parser.add_argument("--limit", type=int, default=None)
    init_parser = commands.add_parser("init", help="Initialize a clean CommerceTrace environment")
    init_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    init_parser.add_argument("--no-data", action="store_true", help="Skip data generation")
    init_parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Generate data only when no initialized dataset exists",
    )
    return root


async def main() -> None:
    """解析命令行参数并分派到对应的异步或同步操作。"""

    args = parser().parse_args()
    settings = Settings()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "generate-data":
        migrate(settings)
        generate_data(settings, profile=args.profile)
    elif args.command == "evaluate":
        await evaluate(settings, limit=args.limit)
    elif args.command == "ablation":
        await ablation(settings, limit=args.limit)
    elif args.command == "init":
        migrate(settings)
        if args.no_data:
            print("Environment initialized without data.")
        elif args.if_empty and dataset_exists(settings):
            print("Environment already contains data; skipped data generation.")
        else:
            generate_data(settings, profile=args.profile)
            print("Environment initialized with data.")
    else:
        raise NotImplementedError(f"unknown command: {args.command}")


def main_sync() -> None:
    """从同步命令行入口启动异步主函数。"""

    asyncio.run(main())
