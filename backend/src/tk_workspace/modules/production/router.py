"""M4 拍摄包与视频审核接口。"""

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from tk_workspace.config import get_settings
from tk_workspace.modules.collaborations.service import CollabError
from tk_workspace.modules.production import ai, service
from tk_workspace.modules.production.schemas import (
    BriefGenerateIn,
    BriefSaveIn,
    BriefVersionView,
    ConfirmBriefIn,
    FeedbackIn,
    FeedbackMessageIn,
    FeedbackMessageView,
    FeedbackView,
    ProductionView,
    ReviewIn,
    RoundIn,
    RoundView,
    VideoVersionView,
)
from tk_workspace.platform import media

router = APIRouter(tags=["production"])


def _err(e: CollabError) -> HTTPException:
    return HTTPException(e.status, detail={"code": e.code, "message": e.message, **e.extra})


def _call(fn, *args):
    try:
        return fn(*args)
    except CollabError as e:
        raise _err(e) from e


@router.get(
    "/collaborations/{collab_id}/production",
    response_model=ProductionView,
    summary="拍摄包版本与各轮视频（合作详情页的“拍摄与视频”）",
)
def production(collab_id: str) -> ProductionView:
    return _call(service.get_production, collab_id)


@router.post(
    "/collaborations/{collab_id}/brief-versions/generate",
    response_model=BriefVersionView,
    status_code=201,
    summary="AI 生成拍摄包新版本（目标语言 + 中文对照）；不含收件信息",
)
def brief_generate(collab_id: str, body: BriefGenerateIn) -> BriefVersionView:
    return _call(ai.generate_brief, collab_id, body)


@router.post(
    "/collaborations/{collab_id}/brief-versions",
    response_model=BriefVersionView,
    status_code=201,
    summary="手动修改拍摄包：以某个版本为基础保存为新版本（旧版本不变）",
)
def brief_save(collab_id: str, body: BriefSaveIn) -> BriefVersionView:
    return _call(service.save_brief, collab_id, body)


@router.post(
    "/collaborations/{collab_id}/brief/confirm",
    response_model=RoundView,
    summary="确认拍摄包：写入当前轮次（没有则开始新一轮），本轮拍摄包从此锁定",
)
def brief_confirm(collab_id: str, body: ConfirmBriefIn) -> RoundView:
    return _call(service.confirm_brief, collab_id, body)


@router.post(
    "/collaborations/{collab_id}/rounds",
    response_model=RoundView,
    status_code=201,
    summary="开始新一轮拍摄（1 轮 = 1 条新视频）；已有未确认拍摄包的轮次时返回它",
)
def round_start(collab_id: str, body: RoundIn, response: Response) -> RoundView:
    view, created = _call(service.start_round, collab_id, body)
    if not created:
        response.status_code = 200
    return view


@router.get("/rounds/{round_id}", response_model=RoundView, summary="某一轮的拍摄包与视频版本")
def round_get(round_id: str) -> RoundView:
    return _call(service.get_round, round_id)


@router.post("/rounds/{round_id}/cancel", response_model=RoundView, summary="取消这一轮（不计数）")
def round_cancel(round_id: str) -> RoundView:
    return _call(service.cancel_round, round_id)


@router.post(
    "/rounds/{round_id}/videos",
    response_model=VideoVersionView,
    status_code=201,
    summary="上传视频（请求体就是文件本身）；同一文件重复上传返回已有版本",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {m: {"schema": {"type": "string", "format": "binary"}} for m in media.ALLOWED_MIME},
        }
    },
)
async def video_upload(
    round_id: str,
    request: Request,
    response: Response,
    x_filename: Annotated[str, Header(alias="X-Filename", max_length=1000)],
    note: Annotated[str, Query(max_length=500)] = "",
) -> VideoVersionView:
    name = unquote(x_filename).strip() or "video"
    mime = media.guess_mime(name, request.headers.get("content-type"))
    if not mime:
        raise _err(CollabError("unsupported_media", "只支持 MP4 / MOV / WebM / M4V 视频", 415))
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > media.MAX_BYTES:
        raise _err(CollabError("file_too_large", "单个视频不能超过 2 GB", 413))
    try:
        await run_in_threadpool(service.precheck_upload, round_id)
    except CollabError as e:
        raise _err(e) from e
    inc = media.Incoming(get_settings().files_dir)
    try:
        async for chunk in request.stream():
            if chunk:
                inc.write(chunk)
        if inc.size == 0:
            raise CollabError("empty_file", "上传的文件是空的", 422)
        sha, rel = inc.finish(mime)
    except media.TooLarge as e:
        inc.discard()
        raise _err(CollabError("file_too_large", "单个视频不能超过 2 GB", 413)) from e
    except CollabError as e:
        inc.discard()
        raise _err(e) from e
    except BaseException:
        inc.discard()
        raise
    try:
        view, created = await run_in_threadpool(
            lambda: service.register_video(
                round_id, sha=sha, rel_path=rel, size=inc.size, mime=mime, original_name=name, note=note
            )
        )
    except CollabError as e:
        raise _err(e) from e
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return view


@router.get("/video-versions/{vid}", response_model=VideoVersionView, summary="视频版本（含反馈与审核结论）")
def video_get(vid: str) -> VideoVersionView:
    return _call(service.get_video, vid)


@router.post(
    "/video-versions/{vid}/feedback",
    response_model=FeedbackView,
    status_code=201,
    summary="添加时间码反馈（审核结论提交前）",
)
def feedback_add(vid: str, body: FeedbackIn) -> FeedbackView:
    return _call(service.add_feedback, vid, body)


@router.delete("/feedback-items/{fid}", status_code=204, summary="删除还没提交的反馈")
def feedback_delete(fid: str) -> Response:
    _call(service.delete_feedback, fid)
    return Response(status_code=204)


@router.post(
    "/video-versions/{vid}/feedback-message",
    response_model=FeedbackMessageView,
    summary="把时间码反馈写成发给达人的消息（目标语言 + 中文对照）；只生成，不代发",
)
def feedback_message(vid: str, body: FeedbackMessageIn) -> FeedbackMessageView:
    return _call(ai.feedback_message, vid, body)


@router.post(
    "/video-versions/{vid}/review",
    response_model=RoundView,
    summary="审核结论：要求返修（至少 1 条反馈）或验收通过（本轮计 1 条）",
)
def video_review(vid: str, body: ReviewIn) -> RoundView:
    return _call(service.review, vid, body)


@router.get(
    "/media/{asset_id}",
    summary="播放视频（签名地址，支持 Range）",
    response_class=FileResponse,
    include_in_schema=False,
)
def media_file(asset_id: str) -> FileResponse:
    path, mime = _call(service.asset_file, asset_id)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})
