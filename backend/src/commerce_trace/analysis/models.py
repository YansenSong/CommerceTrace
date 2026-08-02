from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..models import Chart, QueryTrace, Usage, utc_now


class AnalysisRunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AnalysisStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class AnalysisStepDraft(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    completion_conditions: list[str] = Field(min_length=1, max_length=5)


class CompletionConditionResult(BaseModel):
    condition: str
    satisfied: bool
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str


class AnalysisStep(AnalysisStepDraft):
    model_config = ConfigDict(validate_assignment=True)

    step_id: str = Field(default_factory=lambda: f"step_{uuid4().hex[:12]}")
    status: AnalysisStepStatus = AnalysisStepStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list)
    completion_results: list[CompletionConditionResult] = Field(default_factory=list)
    error: str | None = None


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    revision: int = 1
    revision_reason: str | None = None
    steps: list[AnalysisStep]


class AnalysisEvidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"evidence_{uuid4().hex[:12]}")
    step_id: str
    query_id: str
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_query(
        cls,
        *,
        step_id: str,
        query_id: str,
        summary: str,
        facts: dict[str, Any],
    ) -> AnalysisEvidence:
        return cls(
            step_id=step_id,
            query_id=query_id,
            summary=summary,
            facts=facts,
        )


class AnalysisRun(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    conversation_id: str
    user_id: str
    question: str
    status: AnalysisRunStatus = AnalysisRunStatus.QUEUED
    plan: AnalysisPlan | None = None
    evidence: list[AnalysisEvidence] = Field(default_factory=list)
    queries: list[QueryTrace] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    answer: str | None = None
    error: str | None = None
    usage: Usage = Field(default_factory=Usage)
    plan_revision_count: int = 0
    event_sequence: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AnalysisEvent(BaseModel):
    run_id: str
    sequence: int
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
