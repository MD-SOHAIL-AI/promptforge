"""Additive public contracts shared by desktop, CLI and engine clients."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from backend.agent_runtime.tool_contracts import ToolName


class EngineTool(BaseModel):
    name: ToolName
    provider_name: str
    description: str
    arguments: dict[str, Any]
    stages_files: bool
    executes_process: bool
    capability_profiles: list[str]


class EngineToolCatalog(BaseModel):
    schema_version: Literal[1] = 1
    execution_authority: Literal["python"] = "python"
    availability: str = "Tools additionally require the current run's capability and host prerequisites."
    hardware_actions: str = "Flash and serial connection are host API actions with explicit one-time approval."
    tools: list[EngineTool]


class UploadImage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["bootloader", "partitions", "ota_data", "application"]
    offset: int = Field(ge=0, lt=16 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=16 * 1024 * 1024)
    filename: str


class FirmwareUploadManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1]
    chip: Literal["esp32", "esp32s3", "esp32c3"]
    environment: str = Field(min_length=1, max_length=128)
    board: str = Field(min_length=1, max_length=128)
    toolchain: dict[str, Any]
    images: list[UploadImage] = Field(min_length=3, max_length=4)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_root: str = Field(min_length=1, max_length=4096)


class RequirementReference(BaseModel):
    path: str
    quote: str


class RequirementCheck(BaseModel):
    id: str
    status: str
    evidence: list[RequirementReference] = Field(default_factory=list)
    review_reason: str | None = None


class AcceptanceCheck(BaseModel):
    id: str
    kind: str
    criterion: str
    passed: bool
    status: str | None = None


class AcceptanceReport(BaseModel):
    passed: bool
    checks: list[AcceptanceCheck]


class VerificationReport(BaseModel):
    schema_version: int = 2
    task_revision: str | None = None
    workspace_revision: str | None = None
    references_valid: bool = False
    valid: bool = False
    verification_level: str = "source_reference"
    hardware_verified: bool = False
    checks: list[RequirementCheck] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    semantic_review: dict[str, Any] | None = None
    acceptance: AcceptanceReport | None = None
