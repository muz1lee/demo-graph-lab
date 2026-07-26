from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class SegmentPrediction(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    start_sec: float = Field(validation_alias=AliasChoices("start_sec", "start_time_sec", "start"))
    end_sec: float = Field(validation_alias=AliasChoices("end_sec", "end_time_sec", "end"))
    subtask: str = Field(min_length=1, validation_alias=AliasChoices("subtask", "label", "action"))
    actor_arm: str | None = None
    receiver_arm: str | None = None
    eef_event: str | None = None
    motion_type: str | None = None
    manipulated_object: str | None = None
    target_object: str | None = None
    target_role: str | None = None
    requires_bimanual: bool = False
    requires_alignment: bool = False
    role: str = "core"
    confidence: float | None = None
    visual_evidence: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    method_note: str | None = None

    @field_validator("start_sec", "end_sec")
    @classmethod
    def finite_time(cls, value: float) -> float:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("timestamp must be finite")
        return float(value)

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return max(0.0, min(1.0, float(value)))

    @field_validator("risk_flags", mode="before")
    @classmethod
    def list_or_empty(cls, value: object) -> object:
        return [] if value is None else value


class SegmentationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    demonstration_method: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    segments: list[SegmentPrediction]

    @field_validator("demonstration_method", "quality_warnings", mode="before")
    @classmethod
    def top_level_list_or_empty(cls, value: object) -> object:
        return [] if value is None else value


class VideoItem(BaseModel):
    task_id: str
    task_class: str
    instruction: str = ""
    video_path: str
    source: str = "local"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("video_path")
    @classmethod
    def video_exists(cls, value: str) -> str:
        path = Path(value)
        if not path.exists():
            raise ValueError(f"video_path does not exist: {path}")
        return str(path)


class Manifest(BaseModel):
    schema_version: str = "0.1"
    source: str = "local"
    videos: list[VideoItem]


class VideoInfo(BaseModel):
    path: str
    duration_sec: float | None = None


class SegmentEvidence(BaseModel):
    contact_sheets: list[str] = Field(default_factory=list)
    timestamps: list[float] = Field(default_factory=list)


class TraceSegment(BaseModel):
    index: int
    start_sec: float
    end_sec: float
    label: str
    seed_label: str | None = None
    actor_arm: str | None = None
    receiver_arm: str | None = None
    eef_event: str | None = None
    motion_type: str | None = None
    manipulated_object: str | None = None
    target_object: str | None = None
    target_role: str | None = None
    requires_bimanual: bool = False
    requires_alignment: bool = False
    role: str = "core"
    confidence: float | None = None
    visual_evidence: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    method_note: str | None = None
    evidence: SegmentEvidence = Field(default_factory=SegmentEvidence)


class Trace(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    schema_version: str = "0.1"
    trace_id: str
    task_id: str
    task_class: str
    instruction: str = ""
    video: VideoInfo
    demonstration_method: list[str] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)
    segments: list[TraceSegment]
    model: str
    provider: str
    config: dict[str, Any] = Field(default_factory=dict)
    raw_response_path: str | None = None


class OperationPhaseTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    entry_state: list[str] = Field(default_factory=list)
    exit_state: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    observable_evidence: list[str] = Field(default_factory=list)
    optional: bool = False

    @field_validator(
        "entry_state",
        "exit_state",
        "constraints",
        "observable_evidence",
        mode="before",
    )
    @classmethod
    def phase_template_list_or_single(cls, value: object) -> object:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


class CanonicalOperationProcedure(BaseModel):
    model_config = ConfigDict(extra="ignore")

    procedure_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    reusable_intent: str = Field(min_length=1)
    parameters: list[str] = Field(default_factory=list)
    phase_template: list[OperationPhaseTemplate] = Field(default_factory=list)

    @field_validator("parameters", mode="before")
    @classmethod
    def parameters_list_or_single(cls, value: object) -> object:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


class ObservedOperationPhase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phase_ref: str | None = None
    start_sec: float
    end_sec: float
    description: str = Field(min_length=1)
    evidence_basis: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float | None = None

    @field_validator("evidence_basis", "evidence_refs", mode="before")
    @classmethod
    def evidence_list_or_single(cls, value: object) -> object:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @field_validator("confidence")
    @classmethod
    def phase_confidence_range(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return max(0.0, min(1.0, float(value)))


class OperationInstance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instance_id: str = Field(min_length=1)
    procedure_ref: str = Field(min_length=1)
    start_sec: float
    end_sec: float
    bindings: dict[str, str] = Field(default_factory=dict)
    phases: list[ObservedOperationPhase] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    evidence_gaps: list[str | dict[str, Any]] = Field(default_factory=list)

    @field_validator("deviations", "evidence_gaps", mode="before")
    @classmethod
    def instance_list_or_single(cls, value: object) -> object:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


class OperationStructureResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_procedures: list[CanonicalOperationProcedure]
    instances: list[OperationInstance]
    sequence: list[str] = Field(default_factory=list)
    evidence_gaps: list[str | dict[str, Any]] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)

    @field_validator("sequence", "evidence_gaps", "quality_warnings", mode="before")
    @classmethod
    def response_list_or_single(cls, value: object) -> object:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


def model_to_json(model: BaseModel) -> str:
    return model.model_dump_json(indent=2)
