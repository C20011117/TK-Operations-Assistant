from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response

from tk_workspace.modules.matching import service
from tk_workspace.modules.matching.schemas import (
    CompetitorSuggestionsIn,
    CompetitorSuggestionsView,
    ManualImportIn,
    RecommendationsView,
    RunView,
)

router = APIRouter(tags=["matching"])
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=200)]


def _call(fn, *args):
    try:
        return fn(*args)
    except service.MatchingError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.extra}) from e


@router.post(
    "/campaign-markets/{cm_id}/matching-runs",
    response_model=RunView,
    status_code=202,
    summary="启动匹配（FastMoss 搜索）；同一 Idempotency-Key 重复提交返回同一次运行",
)
def start(cm_id: str, idempotency_key: IdemKey, response: Response) -> RunView:
    run, created = _call(service.start_run, cm_id, idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return run


@router.post(
    "/campaign-markets/{cm_id}/manual-import",
    response_model=RunView,
    status_code=202,
    summary="人工导入候选名单（CSV），用于没有 FastMoss 数据的站点",
)
def manual_import(cm_id: str, body: ManualImportIn, idempotency_key: IdemKey, response: Response) -> RunView:
    run, created, problems = _call(service.import_candidates, cm_id, body, idempotency_key)
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    if problems:
        response.headers["X-Import-Problems"] = str(len(problems))
    return run


@router.get("/campaign-markets/{cm_id}/matching-runs", response_model=list[RunView], summary="匹配运行历史")
def runs_index(cm_id: str) -> list[RunView]:
    return service.list_runs(cm_id)


@router.get("/matching-runs/{run_id}", response_model=RunView, summary="匹配运行详情（进度、调用记录、额度）")
def run_show(run_id: str) -> RunView:
    return _call(service.get_run, run_id)


@router.get(
    "/matching-runs/{run_id}/recommendations",
    response_model=RecommendationsView,
    summary="推荐卡（冻结的快照）",
)
def recommendations(run_id: str) -> RecommendationsView:
    return _call(service.get_recommendations, run_id)


@router.post("/matching-runs/{run_id}/cancel", response_model=RunView, summary="取消匹配")
def cancel(run_id: str) -> RunView:
    return _call(service.cancel_run, run_id)


@router.post(
    "/campaign-markets/{cm_id}/competitor-suggestions",
    response_model=CompetitorSuggestionsView,
    summary="按产品类目推荐本站点的同类在售商品作为竞品（FastMoss，每次 1 额度）",
)
def competitor_suggestions(cm_id: str, body: CompetitorSuggestionsIn) -> CompetitorSuggestionsView:
    return _call(service.suggest_competitors, cm_id, body.keywords)
