from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
Group = Literal["qualified", "needs_verification", "excluded"]


class ProviderCallView(BaseModel):
    id: str
    operation: str
    keywords: str | None
    page: int | None
    status: str
    credit_cost: int | None
    result_count: int | None
    total: int | None
    remaining_credits: int | None
    error: str | None
    created_at: str


class RunView(BaseModel):
    id: str
    campaign_market_id: str
    source: Literal["fastmoss", "manual_import"]
    market_code: str
    provider_region: str | None = Field(description="实际传给 FastMoss 的地区码（英国为 GB）")
    reporting_currency: str
    status: RunStatus
    stage: str | None
    target_list_size: int
    cost_cap_credits: int | None
    credits_used: int
    llm_input_tokens: int
    llm_output_tokens: int
    counters: dict[str, Any]
    stop_reason: str | None
    stop_message: str | None
    error: dict[str, Any] | None
    criteria_version_no: int
    product_version_no: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    calls: list[ProviderCallView] = []


class EvidenceRef(BaseModel):
    key: str
    label: str
    value: str
    state: str | None = None


class CardPoint(BaseModel):
    text: str
    source: Literal["rule", "ai"]
    tag: str | None = None
    evidence: list[EvidenceRef] = []


class CardNote(BaseModel):
    text: str
    tag: str | None = None
    keys: list[str] = []


class Metric(BaseModel):
    label: str
    value: str | None
    state: Literal["known", "unknown", "anomaly", "inconsistent"]
    currency: str | None = None
    note: str = ""


class CreatorRef(BaseModel):
    id: str
    unique_id: str | None
    nickname: str | None
    region: str | None
    profile_url: str | None


class AIView(BaseModel):
    status: Literal["pending", "ok", "failed", "skipped", "not_needed"]
    product_fit: Literal["high", "medium", "low", "unknown"] | None
    summary: str | None
    unsupported: list[str] = Field(default_factory=list, description="模型给出但没有证据支持的推断")


class RecommendationCard(BaseModel):
    evaluation_id: str
    rank: int
    group: Group
    hard_status: Literal["pass", "fail", "unknown"]
    soft_score: int | None
    creator: CreatorRef
    metrics: dict[str, Metric]
    has_email: bool | None
    matches: list[CardPoint]
    mismatches: list[CardPoint]
    unknowns: list[CardNote]
    anomalies: list[CardNote]
    questions: list[str]
    ai: AIView
    profile_text: str | None = None
    categories: str | None = None


class SnapshotView(BaseModel):
    id: str
    counts: dict[str, int]
    limitations: list[str]
    versions: dict[str, Any]
    created_at: str


class RecommendationsView(BaseModel):
    run: RunView
    snapshot: SnapshotView | None
    cards: list[RecommendationCard]


class CompetitorSuggestionsIn(BaseModel):
    keywords: str | None = Field(None, max_length=100, description="可选：竞品关键词（英文效果更好）")


class CompetitorSuggestion(BaseModel):
    product_id: str
    title: str | None
    category_path: str | None
    currency: str | None
    price_display: str | None
    day28_units_sold: str | None
    day28_gmv: str | None
    linked_creator_count: str | None
    shop_name: str | None


class CompetitorSuggestionsView(BaseModel):
    items: list[CompetitorSuggestion]
    credits_used: int = Field(description="本次推荐消耗的 FastMoss 额度")
    category_path: str | None = Field(description="按哪个产品类目推荐；为空表示只按关键词")


class ManualImportIn(BaseModel):
    csv: str = Field(..., max_length=500_000, description="CSV 文本，第一行为表头")
    filename: str = Field("", max_length=200)


class ImportProblem(BaseModel):
    line: int
    message: str
