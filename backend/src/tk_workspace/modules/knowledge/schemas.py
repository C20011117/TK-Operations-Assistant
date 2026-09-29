"""产品档案的接口模型。金额与百分比一律用十进制字符串传输，不用浮点数。"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

Money = Annotated[str, StringConstraints(pattern=r"^\d{1,9}(\.\d{1,2})?$")]
Percent = Annotated[str, StringConstraints(pattern=r"^(100(\.0{1,2})?|\d{1,2}(\.\d{1,2})?)$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

SamplePolicy = Literal["free", "paid", "none", "unknown"]
PriceStatus = Literal["known", "unknown"]
VersionStatus = Literal["draft", "confirmed", "superseded"]


class ProductCategory(BaseModel):
    """TikTok 商品类目（来自 FastMoss 类目识别，用户确认）。按类目找达人时使用最深的一级。"""

    l1_id: int = Field(..., ge=1)
    l2_id: int | None = Field(None, ge=1)
    l3_id: int | None = Field(None, ge=1)
    path: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] = Field(
        ..., description="中文类目路径，如“手机与数码-摄影摄像-监控摄像设备”"
    )

    @model_validator(mode="after")
    def _levels(self):
        if self.l3_id is not None and self.l2_id is None:
            raise ValueError("有三级类目时必须有二级类目")
        return self


class CategorySuggestionsIn(BaseModel):
    query: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]] = (
        Field(..., min_length=1, max_length=5, description="产品名称或品类词，中英文均可")
    )


class CategorySuggestion(BaseModel):
    l1_id: int
    l2_id: int | None
    l3_id: int | None
    name: str | None
    path: str | None
    score: str | None
    matched_query: str | None


class ProductFacts(BaseModel):
    """规范事实。影响筛选与拍摄包的内容都在这里，确认后不可修改。"""

    summary: LongText = Field("", description="一句话介绍产品是什么、解决什么问题")
    selling_points: list[Item] = Field(default_factory=list, max_length=20, description="主要卖点")
    use_scenarios: list[Item] = Field(default_factory=list, max_length=20, description="使用场景")
    target_customers: LongText = Field("", description="目标客户")
    forbidden_claims: list[Item] = Field(
        default_factory=list, max_length=30, description="禁用表述（不得宣称的功效等）"
    )
    reference_links: list[Annotated[str, StringConstraints(pattern=r"^https?://", max_length=500)]] = Field(
        default_factory=list, max_length=20, description="参考链接（商品页、参考视频）"
    )
    notes: LongText = ""
    category: ProductCategory | None = Field(None, description="TikTok 商品类目；用于按类目找达人")


class MarketTermIn(BaseModel):
    market_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    price_status: PriceStatus
    price_amount: Money | None = None
    sample_policy: SamplePolicy = "unknown"
    commission_min_pct: Percent | None = None
    commission_max_pct: Percent | None = None
    quote_valid_until: date | None = None
    notes: ShortText = ""

    @model_validator(mode="after")
    def _check(self):
        if self.price_status == "known" and self.price_amount is None:
            raise ValueError("价格已知时必须填写金额；不知道价格请选择“未知”，不要填 0")
        if self.price_status == "unknown" and self.price_amount is not None:
            raise ValueError("价格为未知时不能填写金额")
        if self.commission_min_pct is not None and self.commission_max_pct is not None:
            from decimal import Decimal

            if Decimal(self.commission_min_pct) > Decimal(self.commission_max_pct):
                raise ValueError("佣金下限不能高于上限")
        return self


class MarketTerm(MarketTermIn):
    price_currency: str = Field(description="站点结算币种，由站点目录决定，不能手填")


class DraftIn(BaseModel):
    facts: ProductFacts
    market_terms: list[MarketTermIn] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _unique_markets(self):
        codes = [t.market_code for t in self.market_terms]
        if len(codes) != len(set(codes)):
            raise ValueError("同一站点只能有一条价格条款")
        return self


class ProductVersion(BaseModel):
    id: str
    version_no: int
    status: VersionStatus
    facts: ProductFacts
    market_terms: list[MarketTerm]
    content_hash: str
    created_at: str
    confirmed_at: str | None


class VersionSummary(BaseModel):
    id: str
    version_no: int
    status: VersionStatus
    created_at: str
    confirmed_at: str | None


class ProductCreate(BaseModel):
    sku: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ProductUpdate(ProductCreate):
    pass


class ProductSummary(BaseModel):
    id: str
    sku: str
    name: str
    status: Literal["active", "archived"]
    current_version_no: int | None
    has_draft: bool
    market_codes: list[str] = Field(description="当前确认版本中有价格条款的站点")
    updated_at: str


class ProductDetail(ProductSummary):
    current: ProductVersion | None
    draft: ProductVersion | None
    versions: list[VersionSummary]
