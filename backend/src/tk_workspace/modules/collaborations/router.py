from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response

from tk_workspace.modules.collaborations import service
from tk_workspace.modules.collaborations.schemas import (
    AgreementIn,
    CollaborationCreate,
    CollaborationDetail,
    CollaborationSummary,
    CollabStatus,
    ConfirmationPreview,
    ConfirmIn,
    DecisionIn,
    DecisionView,
    DispatchIn,
    RecipientView,
    ShipmentEventIn,
    ShipmentIn,
    ShipmentView,
    TransitionIn,
)
from tk_workspace.platform import crypto

router = APIRouter(tags=["collaborations"])
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]


def _call(fn, *args):
    try:
        return fn(*args)
    except service.CollabError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.extra}) from e
    except crypto.PiiKeyError as e:
        raise HTTPException(409, detail={"code": "pii_key_unavailable", "message": str(e)}) from e


def _replayed(response: Response, created: bool) -> None:
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"


# ---------------- 候选决定 ----------------


@router.post(
    "/campaign-markets/{cm_id}/creator-decisions",
    response_model=DecisionView,
    status_code=201,
    summary="记录对候选的决定（保留 / 待核实 / 排除 / 重新考虑）；追加保存，不覆盖历史",
)
def decide(cm_id: str, body: DecisionIn) -> DecisionView:
    return _call(service.record_decision, cm_id, body)


@router.get(
    "/campaign-markets/{cm_id}/creator-decisions",
    response_model=list[DecisionView],
    summary="本任务站点每位达人的当前决定",
)
def decisions(cm_id: str) -> list[DecisionView]:
    return _call(service.list_decisions, cm_id)


# ---------------- 合作 ----------------


@router.post(
    "/campaign-markets/{cm_id}/collaborations",
    response_model=CollaborationDetail,
    status_code=201,
    summary="准备合作（需要先标记“保留”）；同一达人在同一任务站点只有一条合作，重复提交返回已有的",
)
def create_collab(cm_id: str, body: CollaborationCreate, response: Response) -> CollaborationDetail:
    detail, created = _call(service.create_collaboration, cm_id, body)
    if not created:
        response.status_code = 200
    return detail


@router.get("/collaborations", response_model=list[CollaborationSummary], summary="我的合作列表")
def collabs(
    status: Annotated[CollabStatus | None, Query()] = None,
    campaign_market_id: Annotated[str | None, Query()] = None,
) -> list[CollaborationSummary]:
    return service.list_collaborations(status, campaign_market_id)


@router.get(
    "/collaborations/{collab_id}",
    response_model=CollaborationDetail,
    summary="合作详情（状态历史、约定、寄样）",
)
def collab(collab_id: str) -> CollaborationDetail:
    return _call(service.get_collaboration, collab_id)


@router.post(
    "/collaborations/{collab_id}/transitions",
    response_model=CollaborationDetail,
    summary="推进合作状态（联系中 / 洽谈中 / 关闭）；关闭时未寄出的寄样单一并取消",
)
def transition(collab_id: str, body: TransitionIn) -> CollaborationDetail:
    return _call(service.transition, collab_id, body)


@router.post(
    "/collaborations/{collab_id}/confirm-agreement",
    response_model=CollaborationDetail,
    summary="本人记录双方已达成的约定（方式、日期、新视频数量、条款）；之后才能寄样",
)
def agree(collab_id: str, body: AgreementIn) -> CollaborationDetail:
    return _call(service.confirm_agreement, collab_id, body)


# ---------------- 寄样 ----------------


@router.post(
    "/collaborations/{collab_id}/shipments",
    response_model=ShipmentView,
    status_code=201,
    summary="创建寄样单（草稿）；收件信息加密保存；同一 Idempotency-Key 重复提交返回同一张",
)
def create_shipment(
    collab_id: str, body: ShipmentIn, idempotency_key: IdemKey, response: Response
) -> ShipmentView:
    view, created = _call(service.create_shipment, collab_id, body, idempotency_key)
    _replayed(response, created)
    return view


@router.get("/shipments/{shipment_id}", response_model=ShipmentView, summary="寄样单（收件信息已脱敏）")
def shipment(shipment_id: str) -> ShipmentView:
    return _call(service.get_shipment, shipment_id)


@router.put(
    "/shipments/{shipment_id}",
    response_model=ShipmentView,
    summary="修改寄样单（寄出前）；已有的确认作废，需要重新核对确认",
)
def update_shipment(shipment_id: str, body: ShipmentIn) -> ShipmentView:
    return _call(service.update_shipment, shipment_id, body)


@router.post(
    "/shipments/{shipment_id}/confirmation-preview",
    response_model=ConfirmationPreview,
    summary="冻结当前寄样内容，返回核对摘要和哈希（本身不执行任何动作）",
)
def preview(shipment_id: str) -> ConfirmationPreview:
    return _call(service.preview_confirmation, shipment_id)


@router.post(
    "/shipments/{shipment_id}/confirm",
    response_model=ShipmentView,
    summary="本人确认寄样：提交核对时看到的哈希和版本；内容变化或重复确认都会被拒绝",
)
def confirm(shipment_id: str, body: ConfirmIn, idempotency_key: IdemKey, response: Response) -> ShipmentView:
    view, created = _call(service.confirm_shipment, shipment_id, body, idempotency_key)
    _replayed(response, created)
    return view


@router.post(
    "/shipments/{shipment_id}/register-dispatch",
    response_model=ShipmentView,
    summary="人工登记已寄出（使用确认，只能一次）；不调用物流接口，签收状态保持未知",
)
def dispatch(
    shipment_id: str, body: DispatchIn, idempotency_key: IdemKey, response: Response
) -> ShipmentView:
    view, created = _call(service.register_dispatch, shipment_id, body, idempotency_key)
    _replayed(response, created)
    return view


@router.post(
    "/shipments/{shipment_id}/events",
    response_model=ShipmentView,
    status_code=201,
    summary="登记物流节点（运输中 / 已签收 / 异常 / 退回）；按发生时间决定当前签收状态",
)
def add_event(shipment_id: str, body: ShipmentEventIn) -> ShipmentView:
    return _call(service.add_shipment_event, shipment_id, body)


@router.post("/shipments/{shipment_id}/cancel", response_model=ShipmentView, summary="取消寄样单（寄出前）")
def cancel(shipment_id: str) -> ShipmentView:
    return _call(service.cancel_shipment, shipment_id)


@router.get(
    "/shipments/{shipment_id}/recipient",
    response_model=RecipientView,
    summary="查看完整收件信息和快递单号（仅本机界面使用，用于填写快递单）",
)
def recipient(shipment_id: str) -> RecipientView:
    return _call(service.reveal_recipient, shipment_id)
