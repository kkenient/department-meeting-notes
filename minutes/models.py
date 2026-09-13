"""Strict, versioned contract shared by storage, review and exports."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True,
                              json_schema_serialization_defaults_required=True)


Side = Literal["我方", "对方", "第三方", "未知"]
InfoState = Literal["stated", "explicit_none", "uncertain", "conflicting"]

SECTIONS = (
    ("meeting", "一、会议日期及参会人"),
    ("company", "二、对方公司基本情况"),
    ("market", "三、对方公司产品目前市场应用"),
    ("agents", "四、对方目前代理商的情况"),
    ("advantages", "五、对方产品的核心竞争力"),
    ("concerns", "六、对方目前对合作的顾虑"),
    ("customers", "七、对方目前的大客户"),
    ("summary", "八、总结"),
)
KIND_LABELS = {
    "company": "公司基本情况", "market": "市场应用", "agent": "代理商情况",
    "advantage": "核心竞争力", "concern": "合作顾虑", "customer": "客户",
    "commercial": "商务条件", "commitment": "我方承诺", "action": "下一步动作",
    "competitor": "竞品动态", "summary": "会议要点", "issue": "待确认事项",
}
KIND_SECTION = {
    "company": "company", "market": "market", "agent": "agents",
    "advantage": "advantages", "concern": "concerns", "customer": "customers",
    **{k: "summary" for k in ("commercial", "commitment", "action", "competitor", "summary", "issue")},
}
HIGH_RISK = ("commercial", "commitment", "action")


class Evidence(StrictModel):
    segment_id: str
    quote: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


class MetaValue(StrictModel):
    value: str | None = None
    source: Literal["user", "transcript"] = "user"
    evidence: list[Evidence] = Field(default_factory=list)


class Metadata(StrictModel):
    title: MetaValue = Field(default_factory=MetaValue)
    meeting_date: MetaValue = Field(default_factory=MetaValue)
    company: MetaValue = Field(default_factory=MetaValue)

    @model_validator(mode="after")
    def valid_date(self):
        if self.meeting_date.value:
            date.fromisoformat(self.meeting_date.value)
        return self


class Participant(StrictModel):
    participant_id: str
    speaker_label: str
    name: str | None = None
    company: str | None = None
    side: Side = "未知"
    role: str | None = None


class Segment(StrictModel):
    segment_id: str
    text: str = Field(min_length=1)
    order: int = Field(ge=0)
    participant_id: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


class Company(StrictModel):
    kind: Literal["company"]
    field: Literal["营收", "主要产品", "主要应用场景", "公司人数", "其他情况"]
    amount: str | None = None
    currency: str | None = None
    unit: str | None = None
    period: str | None = None
    scope: str | None = None


class Market(StrictModel):
    kind: Literal["market"]
    categories: list[Literal["通信", "消费", "工业", "泛机器人", "新能源", "汽车", "其他", "未明确"]]
    product: str | None = None
    application: str | None = None
    stage: Literal["已量产", "已导入", "测试验证", "计划拓展", "未明确"] = "未明确"


class Agent(StrictModel):
    kind: Literal["agent"]
    name: str | None = None
    product: str | None = None
    region: str | None = None
    cooperation: str | None = None
    policy: str | None = None


class Advantage(StrictModel):
    kind: Literal["advantage"]
    product: str | None = None
    comparison: str | None = None
    condition: str | None = None
    claimed_by: str | None = None


class Concern(StrictModel):
    kind: Literal["concern"]
    raised_by: str | None = None
    condition: str | None = None
    response: str | None = None
    resolved: bool | None = None


class Customer(StrictModel):
    kind: Literal["customer"]
    name: str | None = None
    product: str | None = None
    stage: str | None = None
    scale: str | None = None
    major_confirmed: bool = False
    major_basis: str | None = None


class Commercial(StrictModel):
    kind: Literal["commercial"]
    topic: Literal["报价", "交期", "账期", "返点", "其他"]
    amount: str | None = None
    currency: str | None = None
    unit: str | None = None
    period: str | None = None
    condition: str | None = None
    tax_basis: str | None = None


class Commitment(StrictModel):
    kind: Literal["commitment"]
    owner_id: str | None = None
    owner_side: Side = "未知"
    condition: str | None = None
    original_time: str | None = None
    deadline: date | None = None
    certainty: Literal["明确承诺", "意向", "待确认"] = "待确认"


class Action(StrictModel):
    kind: Literal["action"]
    owner_id: str | None = None
    owner_side: Side = "未知"
    original_time: str | None = None
    deadline: date | None = None
    condition: str | None = None


class Competitor(StrictModel):
    kind: Literal["competitor"]
    name: str | None = None
    product: str | None = None


class Summary(StrictModel):
    kind: Literal["summary"]
    group: Literal["会议要点", "合作进展与分歧"] = "会议要点"


class Issue(StrictModel):
    kind: Literal["issue"]
    reason: str


Details = Annotated[Union[Company, Market, Agent, Advantage, Concern, Customer,
                          Commercial, Commitment, Action, Competitor, Summary, Issue],
                    Field(discriminator="kind")]


class Fact(StrictModel):
    fact_id: str
    statement: str = Field(min_length=1, max_length=4000)
    details: Details
    information_state: InfoState = "stated"
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: Literal["model", "user_added", "user_edited", "demo"] = "model"
    revision: int = Field(default=1, ge=1)
    derived_from_fact_ids: list[str] = Field(default_factory=list)


class Section(StrictModel):
    section_id: str
    title: str
    order: int
    fact_ids: list[str] = Field(default_factory=list)


class Review(StrictModel):
    target_id: str
    revision: int
    content_hash: str
    reviewer: str = "本地用户"
    reviewed_at: str = Field(default_factory=now)
    decision: Literal["confirmed", "unknown_acknowledged"] = "confirmed"


class Correction(StrictModel):
    correction_id: str
    segment_id: str
    original: str
    suggested: str
    reason: str
    status: Literal["pending", "accepted", "rejected"] = "pending"


class AudioSource(StrictModel):
    asset_id: str
    original_name: str
    sha256: str
    size_bytes: int = Field(gt=0)
    duration_ms: int | None = Field(default=None, gt=0)
    transcription_state: Literal["not_started", "complete", "failed"] = "not_started"
    diarization_state: Literal["not_started", "complete", "unavailable", "failed"] = "not_started"
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None
    elapsed_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)
    ambiguous_segment_ids: list[str] = Field(default_factory=list)


class Meeting(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    meeting_id: str = Field(default_factory=lambda: uid("m"))
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "pending_review", "confirmed"] = "draft"
    metadata: Metadata = Field(default_factory=Metadata)
    participants: list[Participant] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=lambda: [
        Section(section_id=sid, title=title, order=i) for i, (sid, title) in enumerate(SECTIONS)
    ])
    reviews: list[Review] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    processing: Literal["not_started", "complete", "failed"] = "not_started"
    mode: Literal["live", "demo", "manual"] = "manual"
    last_error: str | None = None
    audio: AudioSource | None = None


class Quote(StrictModel):
    segment_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(default=0, ge=0)


class CandidateFact(StrictModel):
    fact_id: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=4000)
    details: Details
    information_state: InfoState = "stated"
    evidence: list[Quote] = Field(min_length=1)
    derived_from_fact_ids: list[str] = Field(default_factory=list)


class Extraction(StrictModel):
    facts: list[CandidateFact] = Field(max_length=200)
