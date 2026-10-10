"""Validate financial tool outputs while preserving their JSON representation."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator

from finevolab.ingestion import Evidence


class ReportPassage(Evidence):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: str = Field(min_length=1)
    company: str = Field(min_length=1)
    report_year: int = Field(ge=1900, le=2200)
    section: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    text: str = Field(min_length=1)
    score: float | None = None


class SearchReportsOutput(RootModel[list[ReportPassage]]):
    pass


class ReportArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    html: str = Field(min_length=1)
    pdf: str = Field(min_length=1)
    chart: str | None = Field(default=None, min_length=1)

    @field_validator("html", "pdf")
    @classmethod
    def matching_extension(cls, value, info):
        allowed = {".html", ".htm"} if info.field_name == "html" else {".pdf"}
        if Path(value).suffix.lower() not in allowed:
            raise ValueError("Report artifact extension does not match its format")
        return value


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: str = Field(min_length=1)
