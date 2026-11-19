"""Validated ingestion, trusted identity, and evidence contracts."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Principal(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    company_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    role: Literal["viewer", "operator", "admin"] = "viewer"


class FleetError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class Memory(StrictModel):
    ram_bytes: int = Field(gt=0)
    total_memory_bytes: int = Field(gt=0)
    used_memory_bytes: int = Field(ge=0)
    free_memory_bytes: int = Field(ge=0)
    page_size_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_totals(self):
        if self.used_memory_bytes + self.free_memory_bytes != self.total_memory_bytes:
            raise ValueError("Memory totals do not reconcile")
        if self.ram_bytes != self.total_memory_bytes:
            raise ValueError("RAM and total memory disagree")
        return self


class Volume(StrictModel):
    volume_name: str
    file_system: str
    mount_point: str
    size_bytes: int = Field(gt=0)
    available_bytes: int = Field(ge=0)
    encrypted: bool

    @model_validator(mode="after")
    def valid_space(self):
        if self.available_bytes > self.size_bytes:
            raise ValueError("Available disk exceeds capacity")
        return self


class Battery(StrictModel):
    battery_present: bool
    charging_status: str
    percentage: int = Field(ge=0, le=100)
    condition: str
    cycle_count: int = Field(ge=0)
    full_charge_capacity: int = Field(gt=0)


class Check(StrictModel):
    check_id: str
    status: Literal["pass", "fail"]
    severity: Literal["low", "medium", "high", "critical"]


class OS(StrictModel):
    platform: Literal["darwin", "win32"]
    product_name: str
    product_version: str
    build_version: str
    architecture: str
    kernel_name: str
    kernel_release: str
    hostname: str


class Identity(StrictModel):
    serial_number: str
    model_name: str
    model_identifier: str
    processor: str
    hardware_uuid: str
    total_memory: str


class Software(StrictModel):
    name: str
    version: str
    publisher: str


class Snapshot(StrictModel):
    company_id: str
    device_id: str
    employee_id: str
    collected_at: datetime
    agent_version: str
    os: OS
    device_identity: Identity
    memory: Memory
    disk_volumes: list[Volume] = Field(min_length=1)
    battery: Battery | None = None
    network: list[dict[str, Any]] | None = None
    installed_software: list[Software]
    compliance_results: list[Check]

    @model_validator(mode="after")
    def valid_identity(self):
        if self.collected_at.tzinfo is None:
            raise ValueError("Collection timestamp must include a timezone")
        self.collected_at = self.collected_at.astimezone(timezone.utc)
        if self.device_id != self.device_identity.serial_number:
            raise ValueError("Device ID must agree with serial number")
        ids = [c.check_id for c in self.compliance_results]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate compliance checks")
        mounts = [v.mount_point for v in self.disk_volumes]
        if len(mounts) != len(set(mounts)):
            raise ValueError("Duplicate disk mount points")
        return self


class Evidence(StrictModel):
    source_sha256: str
    source_line: int
    company_id: str
    device_id: str
    collected_at: str
    fields: list[str]


class Finding(StrictModel):
    id: str
    company_id: str
    device_id: str | None
    kind: str
    text: str
    values: dict[str, Any]
    evidence: list[Evidence]
    method: str


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
