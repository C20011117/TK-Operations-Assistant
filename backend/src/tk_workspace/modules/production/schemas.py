"""M4 拍摄包与视频审核：接口数据结构。"""

from typing import Literal

from pydantic import BaseModel, Field

RoundStatus = Literal[
    "briefing", "awaiting_video", "in_review", "revision_requested", "accepted", "cancelled"
]
VideoStatus = Literal["in_review", "changes_requested", "accepted"]
FeedbackCategory = Literal["script", "visual", "audio", "product", "compliance", "other"]
Severity = Literal["must", "should"]
ReviewDecision = Literal["changes_requested", "accepted"]


class BriefGenerateIn(BaseModel):
    language: str | None = Field(None, max_length=8, description="目标语言代码；为空时用站点的常见语言")
    video_length_s: int = Field(30, ge=10, le=180, description="建议视频时长（秒）")
    extra: str = Field("", max_length=1000, description="BD 想补充的要求")


class BriefSaveIn(BaseModel):
    """手动修改：以某个版本为基础保存为新版本。"""

    based_on_version_id: str
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=12000)
    body_zh: str = Field("", max_length=12000)


class BriefVersionView(BaseModel):
    id: str
    version_no: int
    content_language: str
    title: str
    body: str
    body_zh: str
    cautions: list[str]
    source: Literal["ai", "manual"]
    based_on_version_id: str | None
    product_version_id: str
    model: str | None
    locked_round_nos: list[int] = Field(description="锁定了这个版本的轮次")
    created_at: str


class FeedbackIn(BaseModel):
    timecode_ms: int = Field(..., ge=0, le=6 * 3600 * 1000)
    category: FeedbackCategory = "other"
    severity: Severity = "must"
    body: str = Field(..., min_length=1, max_length=2000)


class FeedbackView(BaseModel):
    id: str
    video_version_id: str
    timecode_ms: int
    category: FeedbackCategory
    severity: Severity
    body: str
    submitted: bool = Field(description="已随审核结论提交，不能再修改")
    created_at: str


class ReviewIn(BaseModel):
    decision: ReviewDecision
    summary: str = Field("", max_length=2000)
    message: str | None = Field(None, max_length=6000, description="发给达人的反馈消息（目标语言）")
    message_language: str | None = Field(None, max_length=8)


class ReviewView(BaseModel):
    id: str
    decision: ReviewDecision
    summary: str
    message: str | None
    message_language: str | None
    created_at: str


class FeedbackMessageIn(BaseModel):
    language: str | None = Field(None, max_length=8)
    extra: str = Field("", max_length=1000)


class FeedbackMessageView(BaseModel):
    language: str
    message: str
    message_zh: str
    warnings: list[str]


class VideoVersionView(BaseModel):
    id: str
    round_id: str
    version_no: int
    label: str = Field(description="V1、V2…")
    status: VideoStatus
    original_name: str
    note: str
    size_bytes: int
    mime_type: str
    sha256: str
    media_url: str = Field(description="带签名的播放地址（相对路径，1 小时有效）")
    feedback: list[FeedbackView]
    review: ReviewView | None
    created_at: str


class RoundView(BaseModel):
    id: str
    collaboration_id: str
    round_no: int
    status: RoundStatus
    brief_version: BriefVersionView | None = Field(description="本轮锁定的拍摄包版本")
    brief_confirmed_at: str | None
    videos: list[VideoVersionView]
    accepted_version_id: str | None
    accepted_at: str | None
    counted: bool = Field(description="本轮已验收，计 1 条新视频")
    can_upload: bool
    revision: int
    created_at: str
    updated_at: str


class ProductionView(BaseModel):
    """合作详情页的“拍摄与视频”卡片。"""

    collaboration_id: str
    content_language: str = Field(description="站点默认内容语言")
    content_languages: list[str]
    brief_versions: list[BriefVersionView] = Field(description="新到旧")
    rounds: list[RoundView] = Field(description="新到旧")
    accepted_videos: int
    agreed_video_count: int | None
    can_start_round: bool
    start_round_blocker: str | None


class RoundIn(BaseModel):
    note: str = Field("", max_length=500)


class ConfirmBriefIn(BaseModel):
    brief_version_id: str
    revision: int | None = Field(None, description="轮次的 revision，用于防止并发修改")


class ProductionStage(BaseModel):
    """给合作列表 / 工作台用的拍摄阶段摘要。"""

    round_id: str | None
    round_no: int | None
    round_status: RoundStatus | None
    accepted_videos: int
    last_activity_at: str | None
