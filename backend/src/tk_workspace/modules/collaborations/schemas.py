"""M3 合作与寄样的接口模型。收件信息只在 RecipientIn / RecipientView 中以明文出现，其他视图一律脱敏。"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

Decision = Literal["keep", "needs_verification", "exclude", "reconsider"]
DecisionReason = Literal[
    "good_fit",
    "category_match",
    "competitor_seller",
    "off_target",
    "audience_mismatch",
    "too_small",
    "too_expensive",
    "data_doubt",
    "not_eligible",
    "other",
]
CollabStatus = Literal["planned", "contacting", "negotiating", "agreed", "in_progress", "completed", "closed"]
ClosedReason = Literal["no_reply", "declined", "over_budget", "schedule_conflict", "not_suitable", "other"]
ShipmentKind = Literal["initial_sample", "replacement", "additional_sample"]
ShipmentStatus = Literal["draft", "awaiting_confirmation", "confirmed", "dispatched", "cancelled"]
DeliveryStatus = Literal["unknown", "in_transit", "delivered", "exception", "returned"]

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
OptShort = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


def _decimal_str(v: str | None, what: str) -> str | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        d = Decimal(str(v).strip())
    except InvalidOperation as e:
        raise ValueError(f"{what}必须是数字") from e
    if not d.is_finite() or d < 0:
        raise ValueError(f"{what}必须是不小于 0 的数字")
    return format(d.normalize(), "f")


# ---------------- 候选决定 ----------------


class DecisionIn(BaseModel):
    creator_id: str
    decision: Decision
    reason_code: DecisionReason | None = None
    note: Note = ""
    run_id: str | None = None
    evaluation_id: str | None = None


class DecisionView(BaseModel):
    id: str
    campaign_market_id: str
    creator_id: str
    decision: Decision
    reason_code: str | None
    note: str
    run_id: str | None
    evaluation_id: str | None
    created_at: str
    collaboration_id: str | None = Field(None, description="该达人在本任务站点已有的合作")


# ---------------- 合作 ----------------


class CreatorBrief(BaseModel):
    id: str
    unique_id: str | None
    nickname: str | None
    region: str | None
    profile_url: str | None


class CollaborationCreate(BaseModel):
    creator_id: str
    run_id: str | None = None
    evaluation_id: str | None = None
    note: Note = ""


class TransitionIn(BaseModel):
    to: Literal["contacting", "negotiating", "closed"]
    note: Note = ""
    closed_reason: ClosedReason | None = None
    revision: int | None = Field(None, description="看到的版本号；与当前不一致时拒绝（避免覆盖别处的修改）")

    @model_validator(mode="after")
    def _reason(self) -> "TransitionIn":
        if self.to == "closed" and not self.closed_reason:
            raise ValueError("关闭合作需要选择原因")
        return self


class AgreementIn(BaseModel):
    """本人记录双方已达成的约定：怎么谈成的、哪天、约定了什么。不代表达人已签收或已履约。"""

    agreed_via: Literal["tiktok_message", "email", "whatsapp", "phone", "other"]
    agreed_on: date
    agreed_video_count: int = Field(..., ge=1, le=50, description="约定的新视频数量")
    sample_included: bool = True
    terms_note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    revision: int | None = None


class AgreementView(BaseModel):
    agreed_via: str
    agreed_on: str
    agreed_video_count: int
    sample_included: bool
    terms_note: str
    recorded_at: str


class CollabEventView(BaseModel):
    id: str
    kind: str
    from_status: str | None
    to_status: str | None
    note: str
    created_at: str


class CollaborationSummary(BaseModel):
    id: str
    status: CollabStatus
    closed_reason: str | None
    creator: CreatorBrief
    campaign_id: str
    campaign_name: str
    campaign_market_id: str
    market_code: str
    product_name: str
    product_version_no: int
    agreed_at: str | None
    next_step: str | None = Field(description="下一步该做什么（中文提示）")
    shipment_status: str | None = Field(description="最近一张寄样单的状态")
    delivery_status: str | None
    revision: int
    created_at: str
    updated_at: str


class ConfirmationView(BaseModel):
    id: str
    payload_version: int
    confirmed_at: str
    expires_at: str
    consumed_at: str | None
    revoked_at: str | None
    valid: bool


class ShipmentItemIn(BaseModel):
    sku: Short
    variant: OptShort = None
    quantity: int = Field(..., ge=1, le=100)
    unit_cost: str | None = Field(None, description="单件样品成本（寄样单币种）；不知道就留空，显示为未知")

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _cost(cls, v):
        return _decimal_str(v, "样品成本")


class ShipmentItem(BaseModel):
    sku: str
    variant: str | None
    quantity: int
    unit_cost: str | None


class RecipientIn(BaseModel):
    name: Short
    phone: OptShort = None
    email: OptShort = None
    address_line1: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    address_line2: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    city: Short
    region: OptShort = None
    postcode: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    country_code: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{2}$")
    ]


class ShipmentIn(BaseModel):
    kind: ShipmentKind = "initial_sample"
    items: list[ShipmentItemIn] = Field(..., min_length=1, max_length=10)
    recipient: RecipientIn | None = Field(None, description="创建时必填；修改时留空表示不变")
    cost_cap_amount: str | None = Field(None, description="本次寄样允许的样品 + 运费上限；不知道就留空")
    currency: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Za-z]{3}$")] | None = Field(
        None, description="默认用任务站点的结算币种"
    )
    note: Note = ""

    @field_validator("cost_cap_amount", mode="before")
    @classmethod
    def _cap(cls, v):
        return _decimal_str(v, "费用上限")


class ShipmentEventView(BaseModel):
    id: str
    source: str
    status: str
    occurred_at: str
    observed_at: str
    note: str


class ShipmentView(BaseModel):
    id: str
    collaboration_id: str
    kind: ShipmentKind
    status: ShipmentStatus
    delivery_status: DeliveryStatus = Field(
        description="签收状态：没有物流节点时是 unknown，不会自动变成已签收"
    )
    items: list[ShipmentItem]
    items_cost_total: str | None = Field(description="样品成本合计；任一明细成本未知时为空")
    cost_cap_amount: str | None
    currency: str
    recipient_masked: str = Field(description="脱敏后的收件摘要")
    recipient_country: str | None
    note: str
    payload_version: int
    carrier: str | None
    tracking_masked: str | None
    dispatched_at: str | None
    delivered_at: str | None
    action_status: str | None
    confirmation: ConfirmationView | None
    events: list[ShipmentEventView]
    next_action: Literal["preview", "confirm", "register_dispatch", "record_delivery"] | None
    created_at: str
    updated_at: str


class CollaborationDetail(CollaborationSummary):
    reporting_currency: str
    time_zone: str
    agreement: AgreementView | None
    agreed_video_count: int | None
    allowed_transitions: list[str]
    can_confirm_agreement: bool
    can_create_shipment: bool
    events: list[CollabEventView]
    shipments: list[ShipmentView]


# ---------------- 寄样确认 ----------------


class ConfirmationPreview(BaseModel):
    shipment_id: str
    payload_version: int
    payload_hash: str = Field(description="确认时原样提交；内容变化后哈希不同，旧确认失效")
    summary: dict = Field(description="核对摘要（收件信息已脱敏）")
    expires_in_days: int


class ConfirmIn(BaseModel):
    payload_version: int
    payload_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DispatchIn(BaseModel):
    """人工登记“已寄出”：只记录已发生的事实，不调用任何物流接口。"""

    dispatched_at: datetime | date
    carrier: OptShort = None
    tracking_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)] | None = None
    note: Note = ""


class ShipmentEventIn(BaseModel):
    status: Literal["in_transit", "delivered", "exception", "returned"]
    occurred_at: datetime | date
    note: Note = ""


class RecipientView(BaseModel):
    recipient: RecipientIn
    tracking_number: str | None


def mask_recipient(r: dict) -> str:
    name = (r.get("name") or "").strip()
    pc = (r.get("postcode") or "").strip()
    pc_head = pc.split()[0] if " " in pc else pc[:2]
    return " · ".join(
        p
        for p in (
            f"{name[:1]}***" if name else "",
            f"{pc_head} ***" if pc else "",
            r.get("country_code") or "",
        )
        if p
    )


def mask_tracking(t: str | None) -> str | None:
    if not t:
        return None
    t = re.sub(r"\s+", "", t)
    return f"***{t[-4:]}" if len(t) > 4 else "***"
