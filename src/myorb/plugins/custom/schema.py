"""The JSON a custom source answers with: what one circle around the orb shows.

Kept small and close to WidgetData, so a source's own app decides what is unpaid, overdue or
pending, and myOrb only shows it. Documented in docs/CUSTOM_SOURCES.md.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from myorb.connections.providers import ProviderError

MAX_ROWS = 500


def _web_url(value: str | None) -> str | None:
    # Shown as links: only web addresses, never e.g. javascript: URLs.
    if value is not None and not value.startswith(("https://", "http://")):
        raise ValueError("must be an http(s) address")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class SourceStat(_Model):
    value: int | float | str
    label: str = Field(max_length=40)
    filter: str | None = Field(default=None, max_length=40)


class SourceRow(_Model):
    title: str = Field(max_length=300)
    subtitle: str = Field(default="", max_length=300)
    badge: str = Field(default="", max_length=40)
    tone: Literal["ok", "warn", "fail", "info", "neutral"] = "neutral"
    url: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    group: str = Field(default="", max_length=100)

    _url = field_validator("url")(_web_url)


class SourceLink(_Model):
    label: str = Field(max_length=40)
    url: str = Field(max_length=2000)

    _url = field_validator("url")(_web_url)


class SourceData(_Model):
    version: Literal[1]
    title: str = Field(max_length=60)
    value: int | float | str = Field(union_mode="left_to_right")
    label: str | None = Field(default=None, max_length=30)
    summary: str = Field(default="", max_length=200)
    attention: bool = False
    # How many items need the user (e.g. overdue invoices): the number in the orb when given.
    attention_count: int | None = Field(default=None, ge=0)
    stats: list[SourceStat] = Field(default_factory=list, max_length=8)
    list_title: str = Field(default="", max_length=60)
    group_label: str | None = Field(default=None, max_length=30)
    rows: list[SourceRow] = Field(default_factory=list, max_length=MAX_ROWS)
    link: SourceLink | None = None
    # Sentences for the AI brief; nothing from the source goes to it without them.
    brief: list[str] = Field(default_factory=list, max_length=20)


def parse(payload: object) -> SourceData:
    """The source's answer, or ProviderError saying what is wrong with it (the first few)."""
    try:
        return SourceData.model_validate(payload)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in e['loc']) or 'answer'}: {e['msg']}"
            for e in error.errors()[:3]
        ]
        raise ProviderError("The answer is not in myOrb's format. " + "; ".join(problems)) from None
