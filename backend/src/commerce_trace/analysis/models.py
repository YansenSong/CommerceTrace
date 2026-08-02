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


class AnalysisEventType(StrEnum):
    RUN_CREATED = "run_created"
    PLANNING_STARTED = "planning_started"
    PLAN_PUBLISHED = "plan_published"
    PLAN_REVISED = "plan_revised"
    STEP_STARTED = "step_started"
    STEP_ARTIFACTS_RECORDED = "step_artifacts_recorded"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    RUN_COMPLETED = "run_completed"
    RUN_PARTIAL = "run_partial"
    RUN_FAILED = "run_failed"
    RUN_RETRIED = "run_retried"


class AnalysisStepDraft(BaseModel):
    step_key: str = Field(
        default_factory=lambda: f"planned_{uuid4().hex[:8]}",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]*$",
    )
    depends_on: list[str] = Field(default_factory=list, max_length=8)
    title: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    completion_conditions: list[str] = Field(min_length=1, max_length=5)


class CompletionConditionResult(BaseModel):
    condition: str
    satisfied: bool
    explanation: str


class AnalysisStep(AnalysisStepDraft):
    model_config = ConfigDict(validate_assignment=True)

    step_id: str = Field(default_factory=lambda: f"step_{uuid4().hex[:12]}")
    status: AnalysisStepStatus = AnalysisStepStatus.PENDING
    completion_results: list[CompletionConditionResult] = Field(default_factory=list)
    error: str | None = None


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    revision: int = 1
    revision_reason: str | None = None
    steps: list[AnalysisStep]


class AnalysisRun(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    conversation_id: str
    user_id: str
    question: str
    status: AnalysisRunStatus = AnalysisRunStatus.QUEUED
    plan: AnalysisPlan | None = None
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
    event_type: AnalysisEventType
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
