from fastapi import APIRouter, HTTPException

from tk_workspace.modules.campaigns import service
from tk_workspace.modules.campaigns.criteria import FIELDS, FieldSpec
from tk_workspace.modules.campaigns.schemas import (
    CampaignCreate,
    CampaignDetail,
    CampaignDuplicate,
    CampaignSummary,
    CampaignUpdate,
    CriteriaIn,
    MarketSettingsIn,
)

router = APIRouter(tags=["campaigns"])


def _run(fn, *args):
    try:
        return fn(*args)
    except service.CampaignError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.extra}) from e


@router.get(
    "/criteria/fields", response_model=list[FieldSpec], summary="可用的找人条件字段（含 FastMoss 能力说明）"
)
def criteria_fields() -> list[FieldSpec]:
    return list(FIELDS.values())


@router.get("/campaigns", response_model=list[CampaignSummary], summary="任务列表")
def campaigns_index(product_id: str | None = None, include_archived: bool = False) -> list[CampaignSummary]:
    return service.list_campaigns(product_id, include_archived)


@router.post("/campaigns", response_model=CampaignDetail, status_code=201, summary="新建找人任务（单站点）")
def campaigns_create(body: CampaignCreate) -> CampaignDetail:
    return _run(service.create_campaign, body)


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail, summary="任务详情（含就绪检查）")
def campaigns_detail(campaign_id: str) -> CampaignDetail:
    return _run(service.get_campaign, campaign_id)


@router.put("/campaigns/{campaign_id}", response_model=CampaignDetail, summary="修改任务基本信息")
def campaigns_update(campaign_id: str, body: CampaignUpdate) -> CampaignDetail:
    return _run(service.update_campaign, campaign_id, body)


@router.post(
    "/campaigns/{campaign_id}/duplicate",
    response_model=CampaignDetail,
    status_code=201,
    summary="复制到其他站点",
)
def campaigns_duplicate(campaign_id: str, body: CampaignDuplicate) -> CampaignDetail:
    return _run(service.duplicate_campaign, campaign_id, body)


@router.post("/campaigns/{campaign_id}/archive", response_model=CampaignDetail, summary="归档")
def campaigns_archive(campaign_id: str) -> CampaignDetail:
    return _run(service.set_archived, campaign_id, True)


@router.post("/campaigns/{campaign_id}/restore", response_model=CampaignDetail, summary="取消归档")
def campaigns_restore(campaign_id: str) -> CampaignDetail:
    return _run(service.set_archived, campaign_id, False)


@router.put(
    "/campaign-markets/{cm_id}", response_model=CampaignDetail, summary="修改名单数量、预算、额度上限"
)
def cm_update(cm_id: str, body: MarketSettingsIn) -> CampaignDetail:
    return _run(service.update_market_settings, cm_id, body)


@router.put("/campaign-markets/{cm_id}/criteria", response_model=CampaignDetail, summary="保存条件草稿")
def cm_save_criteria(cm_id: str, body: CriteriaIn) -> CampaignDetail:
    return _run(service.save_criteria, cm_id, body)


@router.delete("/campaign-markets/{cm_id}/criteria", response_model=CampaignDetail, summary="放弃条件草稿")
def cm_discard_criteria(cm_id: str) -> CampaignDetail:
    return _run(service.discard_criteria_draft, cm_id)


@router.post(
    "/campaign-markets/{cm_id}/use-latest-product",
    response_model=CampaignDetail,
    summary="更新到产品最新版本",
)
def cm_use_latest(cm_id: str) -> CampaignDetail:
    return _run(service.use_latest_product_version, cm_id)


@router.post(
    "/campaign-markets/{cm_id}/confirm", response_model=CampaignDetail, summary="确认任务（有缺项时返回 422）"
)
def cm_confirm(cm_id: str) -> CampaignDetail:
    return _run(service.confirm_market, cm_id)
