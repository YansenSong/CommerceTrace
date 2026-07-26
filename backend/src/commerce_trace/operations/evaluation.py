from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from ..agent import Agent
from ..contracts import EventType, StreamEvent


class EvaluationCase(BaseModel):
    id: str
    category: str
    question: str
    expectation: str


class EvaluationDataset(BaseModel):
    version: str
    cases: list[EvaluationCase]


class CaseResult(BaseModel):
    case_id: str
    category: str
    expectation: str
    observed_status: str
    passed: bool
    evidence_count: int = 0
    evidence_complete: bool = False
    tool_iterations: int = 0
    business_sql_calls: int = 0
    llm_calls: int = 0
    token_count: int = 0
    first_sql_succeeded: bool | None = None
    self_correction_succeeded: bool | None = None
    latency_ms: float
    answer: str = ""
    failure_reason: str | None = None


class EvaluationReport(BaseModel):
    run_id: str = Field(default_factory=lambda: f"eval_{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    dataset_version: str
    configuration: dict[str, Any]
    metrics: dict[str, Any]
    results: list[CaseResult]

    def markdown(self) -> str:
        lines = [
            "# CommerceTrace Evaluation Report",
            "",
            f"- Run: `{self.run_id}`",
            f"- Dataset: `{self.dataset_version}`",
            f"- Cases: {self.metrics['case_count']}",
            f"- Pass rate: {self.metrics['pass_rate']:.2%}",
            f"- Evidence completeness: {self.metrics['evidence_completeness']:.2%}",
            f"- Dangerous request block rate: {self.metrics['danger_block_rate']:.2%}",
            f"- Clarification accuracy: {self.metrics['clarification_accuracy']:.2%}",
            f"- First SQL success rate: {self.metrics['first_sql_success_rate']:.2%}",
            f"- Average tool calls: {self.metrics['average_tool_iterations']:.2f}",
            f"- Average LLM calls: {self.metrics['average_llm_calls']:.2f}",
            f"- Total tokens reported by model: {self.metrics['total_tokens']}",
            f"- Average latency: {self.metrics['average_latency_ms']:.1f} ms",
            "",
            "## Category results",
            "",
            "| Category | Passed | Total |",
            "|---|---:|---:|",
        ]
        for category, counts in sorted(self.metrics["categories"].items()):
            lines.append(f"| {category} | {counts['passed']} | {counts['total']} |")
        failures = [result for result in self.results if not result.passed]
        lines.extend(["", "## Failures", ""])
        if not failures:
            lines.append("None.")
        else:
            lines.extend(
                f"- `{result.case_id}`: {result.failure_reason or result.observed_status}"
                for result in failures
            )
        return "\n".join(lines) + "\n"


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


async def run_case(
    *,
    agent: Agent,
    case: EvaluationCase,
    user_id: str,
) -> CaseResult:
    started = time.perf_counter()
    events: list[StreamEvent] = [
        event
        async for event in agent.run(
            user_id=user_id,
            conversation_id=f"eval_conv_{uuid4().hex}",
            request_id=f"eval_req_{uuid4().hex}",
            question=case.question,
        )
    ]
    completed = next(
        (event for event in reversed(events) if event.event is EventType.ANSWER_COMPLETED),
        None,
    )
    if completed is None:
        return CaseResult(
            case_id=case.id,
            category=case.category,
            expectation=case.expectation,
            observed_status="failed",
            passed=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            failure_reason="answer.completed missing",
        )
    payload = completed.payload
    status = str(payload.get("status", "unknown"))
    answer = str(payload.get("answer", ""))
    evidence_ids = [str(item) for item in payload.get("evidence_ids", [])]
    evidence_complete = bool(evidence_ids) and all(
        f"[{evidence_id}]" in answer for evidence_id in evidence_ids
    )
    if case.expectation == "clarification":
        passed = status == "clarification_required"
    elif case.expectation == "refused":
        passed = status == "refused"
    elif case.expectation == "attribution":
        passed = status in {"completed", "partial"} and evidence_complete
    else:
        passed = status == "completed" and evidence_complete
    usage = payload.get("usage", {})
    sql_outcomes = [
        event.event is EventType.TOOL_COMPLETED
        for event in events
        if event.event in {EventType.TOOL_COMPLETED, EventType.TOOL_FAILED}
        and event.payload.get("tool_name") == "run_sql"
    ]
    first_sql_succeeded = sql_outcomes[0] if sql_outcomes else None
    correction_succeeded = (
        any(sql_outcomes[index] for index in range(1, len(sql_outcomes)))
        if sql_outcomes and not sql_outcomes[0]
        else None
    )
    return CaseResult(
        case_id=case.id,
        category=case.category,
        expectation=case.expectation,
        observed_status=status,
        passed=passed,
        evidence_count=len(evidence_ids),
        evidence_complete=evidence_complete,
        tool_iterations=int(usage.get("tool_iterations", 0)),
        business_sql_calls=int(usage.get("business_sql_calls", 0)),
        llm_calls=int(usage.get("llm_calls", 0)),
        token_count=int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0)),
        first_sql_succeeded=first_sql_succeeded,
        self_correction_succeeded=correction_succeeded,
        latency_ms=(time.perf_counter() - started) * 1000,
        answer=answer,
        failure_reason=None if passed else "expectation_not_met",
    )


async def run_evaluation(
    *,
    agent: Agent,
    dataset: EvaluationDataset,
    configuration: dict[str, Any],
    limit: int | None = None,
) -> EvaluationReport:
    selected = dataset.cases[:limit] if limit else dataset.cases
    results = [
        await run_case(agent=agent, case=case, user_id=f"eval_{case.id}") for case in selected
    ]
    category_totals: Counter[str] = Counter(result.category for result in results)
    category_passed: Counter[str] = Counter(result.category for result in results if result.passed)
    evidence_cases = [
        result for result in results if result.expectation in {"evidence", "attribution"}
    ]
    danger_cases = [result for result in results if result.expectation == "refused"]
    clarification_cases = [result for result in results if result.expectation == "clarification"]
    attribution_cases = [result for result in results if result.expectation == "attribution"]
    first_sql_cases = [result for result in results if result.first_sql_succeeded is not None]
    correction_cases = [
        result for result in results if result.self_correction_succeeded is not None
    ]
    count = len(results)
    metrics: dict[str, Any] = {
        "case_count": count,
        "passed": sum(result.passed for result in results),
        "pass_rate": sum(result.passed for result in results) / count if count else 0,
        "evidence_completeness": (
            sum(result.evidence_complete for result in evidence_cases) / len(evidence_cases)
            if evidence_cases
            else 1
        ),
        "danger_block_rate": (
            sum(result.passed for result in danger_cases) / len(danger_cases) if danger_cases else 1
        ),
        "clarification_accuracy": (
            sum(result.passed for result in clarification_cases) / len(clarification_cases)
            if clarification_cases
            else 1
        ),
        "attribution_pass_rate": (
            sum(result.passed for result in attribution_cases) / len(attribution_cases)
            if attribution_cases
            else 1
        ),
        "first_sql_success_rate": (
            sum(bool(result.first_sql_succeeded) for result in first_sql_cases)
            / len(first_sql_cases)
            if first_sql_cases
            else 1
        ),
        "self_correction_rate": (
            sum(bool(result.self_correction_succeeded) for result in correction_cases)
            / len(correction_cases)
            if correction_cases
            else None
        ),
        "average_tool_iterations": (
            sum(result.tool_iterations for result in results) / count if count else 0
        ),
        "average_business_sql_calls": (
            sum(result.business_sql_calls for result in results) / count if count else 0
        ),
        "average_llm_calls": (sum(result.llm_calls for result in results) / count if count else 0),
        "total_tokens": sum(result.token_count for result in results),
        "average_latency_ms": (
            sum(result.latency_ms for result in results) / count if count else 0
        ),
        "categories": {
            category: {
                "passed": category_passed[category],
                "total": total,
            }
            for category, total in category_totals.items()
        },
    }
    return EvaluationReport(
        dataset_version=dataset.version,
        configuration=configuration,
        metrics=metrics,
        results=results,
    )


def write_report(report: EvaluationReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{report.run_id}.json"
    markdown_path = directory / f"{report.run_id}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(report.markdown(), encoding="utf-8")
    return json_path, markdown_path


def write_ablation_report(
    *,
    runs: dict[str, EvaluationReport],
    configuration: dict[str, Any],
    directory: Path,
) -> tuple[Path, Path]:
    run_id = f"ablation_{uuid4().hex[:12]}"
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "configuration": configuration,
        "variants": {name: report.model_dump(mode="json") for name, report in runs.items()},
    }
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{run_id}.json"
    markdown_path = directory / f"{run_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# CommerceTrace Ablation Report",
        "",
        "A–D 使用同一数据、问题切片和版本配置；每一档只增加标题所示能力。",
        "",
        "| Variant | Pass rate | Evidence | Avg SQL | Avg LLM |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, report in runs.items():
        metrics = report.metrics
        lines.append(
            f"| {name} | {metrics['pass_rate']:.2%} | "
            f"{metrics['evidence_completeness']:.2%} | "
            f"{metrics['average_business_sql_calls']:.2f} | "
            f"{metrics['average_llm_calls']:.2f} |"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
