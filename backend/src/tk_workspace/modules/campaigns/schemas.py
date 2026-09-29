from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from tk_workspace.modules.campaigns.criteria import Criterion, SearchInput
from tk_workspace.modules.knowledge.schemas import MarketTerm, Money

Goal = Literal["sales", "content", "awareness"]
CollaborationType = Literal["free_sample", "paid", "commission", "hybrid"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class MarketSettingsIn(BaseModel):
    target_list_size: int | None = Field(None, ge=1, le=500, description="目标名单数量")
    budget_min: Money | None = Field(None, description="预算下限（站点结算币种）")
    budget_max: Money | None = Field(None, description="预算上限（站点结算币种）")
    cost_cap_credits: int | None = Field(None, ge=0, le=100000, description="本任务 FastMoss 额度上限")

    @model_validator(mode="after")
    def _budget(self):
        from decimal import Decimal

        if self.budget_min is not None and self.budget_max is not None:
            if Decimal(self.budget_min) > Decimal(self.budget_max):
                raise ValueError("预算下限不能高于上限")
        return self


class CampaignBase(BaseModel):
    name: Name
    goal: Goal
    collaboration_type: CollaborationType
    start_date: date | None = None
    end_date: date | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""

    @model_validator(mode="after")
    def _dates(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        return self


class CampaignCreate(CampaignBase):
    product_id: str
    market_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    market: MarketSettingsIn = Field(default_factory=MarketSettingsIn)


class CampaignUpdate(CampaignBase):
    pass


class CampaignDuplicate(BaseModel):
    market_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    name: Name | None = None


class CriteriaIn(BaseModel):
    criteria: list[Criterion] = Field(default_factory=list, max_length=30)
    search: SearchInput = Field(default_factory=SearchInput)


class CriteriaVersion(BaseModel):
    id: str
    version_no: int
    status: Literal["draft", "confirmed", "superseded"]
    product_version_id: str
    product_version_no: int
    criteria: list[Criterion]
    search: SearchInput
    created_at: str
    confirmed_at: str | None


class CriteriaVersionSummary(BaseModel):
    id: str
    version_no: int
    status: Literal["draft", "confirmed", "superseded"]
    product_version_no: int
    confirmed_at: str | None


class Issue(BaseModel):
    code: str
    message: str


class Readiness(BaseModel):
    ready: bool = Field(description="没有阻断项，可以确认")
    blockers: list[Issue]
    warnings: list[Issue]


class ProductRef(BaseModel):
    id: str
    sku: str
    name: str


class CampaignMarketView(BaseModel):
    id: str
    market_code: str
    market_name_zh: str
    market_data_status: str
    fastmoss_region: str | None
    content_languages: list[str]
    time_zone: str
    reporting_currency: str
    target_list_size: int | None
    budget_min: str | None
    budget_max: str | None
    cost_cap_credits: int | None
    status: Literal["draft", "ready"]
    confirmed_at: str | None
    product_version_id: str
    product_version_no: int
    latest_product_version_no: int | None = Field(description="产品当前确认版本号；比任务引用的新时提示更新")
    price_term: MarketTerm | None = Field(description="任务引用的产品版本中该站点的价格条款")
    criteria_draft: CriteriaVersion | None
    criteria_current: CriteriaVersion | None
    criteria_history: list[CriteriaVersionSummary]
    readiness: Readiness


class CampaignDetail(BaseModel):
    id: str
    name: str
    goal: Goal
    collaboration_type: CollaborationType
    start_date: date | None
    end_date: date | None
    notes: str
    status: Literal["active", "archived"]
    product: ProductRef
    markets: list[CampaignMarketView]
    created_at: str
    updated_at: str


class CampaignSummary(BaseModel):
    id: str
    name: str
    product_name: str
    product_sku: str
    market_code: str
    market_name_zh: str
    reporting_currency: str
    time_zone: str
    goal: Goal
    market_status: Literal["draft", "ready"]
    status: Literal["active", "archived"]
    start_date: date | None
    end_date: date | None
    updated_at: str
