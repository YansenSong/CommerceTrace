from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg

from .bootstrap import (
    bootstrap_memory,
    load_business_documents,
)
from .config import Settings
from .context import schema_fingerprint
from .evaluation import (
    EvaluationReport,
    load_dataset,
    run_evaluation,
    run_memory_experiment,
    write_ablation_report,
    write_report,
)
from .memory import MemoryService, memory_report
from .memory_index import ChromaMemoryIndex
from .memory_replay import load_golden_cases, replay_memories, write_replay_report
from .postgres import PostgresResources, PostgresSqlExecutor, PostgresStore
from .runtime import FeatureConfiguration, build_runtime
from .sql_safety import SqlSafetyPolicy

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def migrate(settings: Settings) -> None:
    migration_paths = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    if not migration_paths:
        raise RuntimeError("no migrations found")
    with (
        psycopg.connect(settings.database_url, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        for path in migration_paths:
            cursor.execute(path.read_text(encoding="utf-8"))
            print(f"applied {path.name}")


def generate_data(settings: Settings, profile: str) -> dict[str, Any]:
    sys.path.insert(0, str(PROJECT_ROOT))
    from data_generator.generate import (  # type: ignore[import-not-found]
        generate,
        iter_copy_statements,
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
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "TRUNCATE "
                + ", ".join(f"ecommerce.{table}" for table in truncate_order)
                + " RESTART IDENTITY CASCADE"
            )
            for statement, rows in iter_copy_statements(generated):
                with cursor.copy(statement) as copy:
                    for row in rows:
                        copy.write_row(row)
            cursor.execute(
                """
                INSERT INTO agent_app.dataset_metadata
                  (singleton, data_version, seed, profile, result_hash, row_counts)
                VALUES (TRUE, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (singleton) DO UPDATE SET
                  data_version = EXCLUDED.data_version,
                  seed = EXCLUDED.seed,
                  profile = EXCLUDED.profile,
                  result_hash = EXCLUDED.result_hash,
                  row_counts = EXCLUDED.row_counts,
                  generated_at = now()
                """,
                (
                    generated.metadata["version"],
                    generated.metadata["seed"],
                    profile,
                    generated.result_hash,
                    json.dumps(generated.metadata["row_counts"]),
                ),
            )
        connection.commit()
    print(json.dumps(generated.metadata, ensure_ascii=False, indent=2))
    return dict(generated.metadata)


def dataset_exists(settings: Settings) -> bool:
    with psycopg.connect(settings.database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM agent_app.dataset_metadata)")
        row = cursor.fetchone()
    return bool(row and row[0])


async def bootstrap_and_rebuild(settings: Settings, *, rebuild_index: bool) -> None:
    resources = PostgresResources(settings.database_url)
    await resources.open()
    try:
        store = PostgresStore(resources)
        records = await bootstrap_memory(store, project_path(settings.knowledge_path))
        print(json.dumps(memory_report(records), ensure_ascii=False, indent=2))
        if rebuild_index:
            index = ChromaMemoryIndex(project_path(settings.chroma_path), settings.embedding_model)
            tool_count = await index.rebuild(await store.list_memories())
            business_count = await index.rebuild_business(
                load_business_documents(project_path(settings.knowledge_path))
            )
            print(
                json.dumps(
                    {
                        "tool_memory_index": tool_count,
                        "business_memory_index": business_count,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    finally:
        await resources.close()


async def rebuild_index(settings: Settings) -> None:
    resources = PostgresResources(settings.database_url)
    await resources.open()
    try:
        store = PostgresStore(resources)
        index = ChromaMemoryIndex(project_path(settings.chroma_path), settings.embedding_model)
        tool_count = await index.rebuild(await store.list_memories())
        business_count = await index.rebuild_business(
            load_business_documents(project_path(settings.knowledge_path))
        )
        print(
            json.dumps(
                {
                    "tool_memory_index": tool_count,
                    "business_memory_index": business_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await resources.close()


async def replay_memory(settings: Settings) -> None:
    app_resources = PostgresResources(settings.database_url)
    query_resources = PostgresResources(settings.query_database_url, min_size=1, max_size=2)
    await app_resources.open()
    await query_resources.open()
    try:
        store = PostgresStore(app_resources)
        index = ChromaMemoryIndex(project_path(settings.chroma_path), settings.embedding_model)
        service = MemoryService(
            store=store,
            schema_fingerprint=schema_fingerprint(),
            metric_versions={"revenue": "1", "refund_rate": "1", "aov": "1"},
            index=index,
        )
        report = await replay_memories(
            service=service,
            executor=PostgresSqlExecutor(
                query_resources,
                statement_timeout_ms=settings.statement_timeout_ms,
            ),
            policy=SqlSafetyPolicy(
                max_rows=settings.max_result_rows,
                max_distinct_values=settings.max_distinct_values,
            ),
            golden_cases=load_golden_cases(
                project_path(settings.knowledge_path),
                schema_fingerprint=service.schema_fingerprint,
            ),
        )
        paths = write_replay_report(report, PROJECT_ROOT / "reports")
        print("\n".join(str(path) for path in paths))
    finally:
        await query_resources.close()
        await app_resources.close()


def project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


async def evaluate(settings: Settings, *, limit: int | None) -> None:
    runtime = build_runtime(settings)
    for resource in runtime.resources:
        await resource.open()
    try:
        dataset = load_dataset(project_path(settings.eval_dataset_path))
        report = await run_evaluation(
            agent=runtime.agent,
            dataset=dataset,
            configuration={
                "model_mode": settings.llm_mode,
                "model": settings.openai_model if settings.llm_mode == "openai" else "scripted",
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


async def memory_experiment(settings: Settings, *, limit: int) -> None:
    runtime = build_runtime(settings)
    for resource in runtime.resources:
        await resource.open()
    try:
        dataset = load_dataset(project_path(settings.eval_dataset_path))
        base_cases = [case for case in dataset.cases if case.expectation == "evidence"][:limit]
        warm_cases = [
            case.model_copy(
                update={
                    "id": f"warm-{case.id}",
                    "question": f"换一种说法，请分析：{case.question}",
                }
            )
            for case in base_cases
        ]
        result = await run_memory_experiment(
            agent=runtime.agent,
            store=runtime.store,
            cold_cases=base_cases,
            warm_cases=warm_cases,
            configuration={
                "model_mode": settings.llm_mode,
                "schema_version": settings.schema_version,
                "knowledge_version": settings.knowledge_version,
                "data_seed": 20260725,
            },
        )
        output = PROJECT_ROOT / "reports" / "memory-experiment.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        markdown = output.with_suffix(".md")
        markdown.write_text(
            "\n".join(
                [
                    "# CommerceTrace Candidate Cold/Warm Experiment",
                    "",
                    f"- Cold pass rate: {result['cold']['metrics']['pass_rate']:.2%}",
                    f"- Warm pass rate: {result['warm']['metrics']['pass_rate']:.2%}",
                    f"- Warm accuracy delta: {result['warm_accuracy_delta']:.2%}",
                    f"- Candidate recall case rate: {result['candidate_recall_case_rate']:.2%}",
                    f"- Candidate adoption count: {result['candidate_adoption_count']}",
                    f"- Pollution detected: {result['candidate_pollution_detected']}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f"{output}\n{markdown}")
    finally:
        for resource in reversed(runtime.resources):
            await resource.close()


async def ablation(settings: Settings, *, limit: int | None) -> None:
    variants = {
        "A_schema_and_system_prompt": FeatureConfiguration(
            include_knowledge=False,
            include_memory=False,
            include_candidates=False,
            enable_sql_retries=False,
            record_candidates=False,
        ),
        "B_business_rules_and_trusted_sql": FeatureConfiguration(
            include_candidates=False,
            enable_sql_retries=False,
            record_candidates=False,
        ),
        "C_execution_feedback_and_correction": FeatureConfiguration(
            include_candidates=False,
            record_candidates=False,
        ),
        "D_candidate_continuous_memory": FeatureConfiguration(),
    }
    dataset = load_dataset(project_path(settings.eval_dataset_path))
    runs: dict[str, EvaluationReport] = {}
    for name, features in variants.items():
        runtime = build_runtime(settings, features)
        for resource in runtime.resources:
            await resource.open()
        try:
            if not runs:
                await runtime.store.clear_candidates()
            runs[name] = await run_evaluation(
                agent=runtime.agent,
                dataset=dataset,
                configuration={
                    "variant": name,
                    "features": asdict(features),
                    "model_mode": settings.llm_mode,
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
            "model_mode": settings.llm_mode,
            "data_seed": 20260725,
        },
        directory=PROJECT_ROOT / "reports",
    )
    print("\n".join(str(path) for path in paths))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="commerce-trace")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply PostgreSQL migrations")
    generate_parser = commands.add_parser(
        "generate-data", help="Generate and load fixed-seed ecommerce data"
    )
    generate_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    commands.add_parser("bootstrap-memory", help="Load Golden memory from knowledge files")
    commands.add_parser(
        "rebuild-memory-index", help="Rebuild both Chroma collections from authorities"
    )
    commands.add_parser(
        "replay-memory", help="Replay Candidate memory against versioned Golden cases"
    )
    evaluate_parser = commands.add_parser("evaluate", help="Run the reproducible MVP evaluation")
    evaluate_parser.add_argument("--limit", type=int, default=None)
    memory_parser = commands.add_parser(
        "memory-experiment", help="Run Candidate Cold/Warm experiment"
    )
    memory_parser.add_argument("--limit", type=int, default=10)
    ablation_parser = commands.add_parser(
        "ablation", help="Run reproducible A-D architecture ablations"
    )
    ablation_parser.add_argument("--limit", type=int, default=None)
    init_parser = commands.add_parser("init", help="Initialize a clean CommerceTrace environment")
    init_parser.add_argument("--profile", choices=["test", "demo"], default="test")
    init_parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Preserve an already generated dataset",
    )
    return root


def main() -> None:
    args = parser().parse_args()
    settings = Settings()
    if args.command == "migrate":
        migrate(settings)
    elif args.command == "generate-data":
        generate_data(settings, args.profile)
    elif args.command == "bootstrap-memory":
        asyncio.run(bootstrap_and_rebuild(settings, rebuild_index=False))
    elif args.command == "rebuild-memory-index":
        asyncio.run(rebuild_index(settings))
    elif args.command == "replay-memory":
        asyncio.run(replay_memory(settings))
    elif args.command == "evaluate":
        asyncio.run(evaluate(settings, limit=args.limit))
    elif args.command == "memory-experiment":
        asyncio.run(memory_experiment(settings, limit=args.limit))
    elif args.command == "ablation":
        asyncio.run(ablation(settings, limit=args.limit))
    elif args.command == "init":
        migrate(settings)
        if not args.if_empty or not dataset_exists(settings):
            generate_data(settings, args.profile)
        asyncio.run(bootstrap_and_rebuild(settings, rebuild_index=True))
    else:
        raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    main()
