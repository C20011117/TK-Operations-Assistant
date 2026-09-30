"""M4 拍摄包与视频审核。

完成标准：目标语言拍摄包确认后，版本锁定在本轮；V1 反馈精确到时间码；V2 验收后本轮计 1 条。
另外：同一文件重复上传不会产生第二个视频；播放地址需要有效签名；发给模型的内容不含收件信息。
"""

import json
import time
from urllib.parse import quote

import pytest

from tk_workspace.modules.production import ai
from tk_workspace.modules.production.ai import BriefOut, FeedbackOut
from tk_workspace.platform import media
from tk_workspace.platform.jobs.runner import JobRunner

from .test_m3_collaboration import (
    SECRETS,
    _agreed,
    _confirm,
    _count,
    _keep_and_collab,
    _key,
    _new_shipment,
    _setup,
)

V1 = b"\x00\x00\x00\x18ftypmp42" + b"V1-frames" * 2000
V2 = b"\x00\x00\x00\x18ftypmp42" + b"V2-frames" * 2500


@pytest.fixture
def runner(data_dir):
    r = JobRunner(workers=1, poll_seconds=0.05)
    r.start()
    yield r
    r.stop()


@pytest.fixture
def fake_ai(monkeypatch):
    calls: list[dict] = []

    def brief(system, payload):
        calls.append({"kind": "brief", "system": system, "payload": payload})
        n = sum(1 for c in calls if c["kind"] == "brief")
        return (
            BriefOut(
                title=f"Blender reel #{n}",
                body="Hook: morning smoothie in 3s. Shot list: ... Please add #ad and turn on the disclosure toggle.",
                body_zh="钩子：3 秒做好早餐果昔。分镜：…… 请加 #ad 并打开商业内容披露。",
                cautions=["确认视频时长"],
            ),
            "fake-model",
        )

    def feedback(system, payload):
        calls.append({"kind": "feedback", "system": system, "payload": payload})
        tcs = " ".join(f["timecode"] for f in payload["feedback"])
        return FeedbackOut(
            message=f"Hi! Great video. Please fix: {tcs}", message_zh=f"改：{tcs}"
        ), "fake-model"

    monkeypatch.setattr(ai, "brief_model", brief)
    monkeypatch.setattr(ai, "feedback_model", feedback)
    return calls


def _prod(client, co_id):
    r = client.get(f"/api/v1/collaborations/{co_id}/production")
    assert r.status_code == 200, r.text
    return r.json()


def _gen(client, co_id, **body):
    r = client.post(f"/api/v1/collaborations/{co_id}/brief-versions/generate", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, round_id, data, name="clip v1.mp4", ctype="video/mp4"):
    return client.post(
        f"/api/v1/rounds/{round_id}/videos",
        content=data,
        headers={"Content-Type": ctype, "X-Filename": quote(name)},
    )


def _confirmed_round(client, co_id):
    v = _gen(client, co_id)
    r = client.post(f"/api/v1/collaborations/{co_id}/brief/confirm", json={"brief_version_id": v["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def _in_progress(client, runner):
    """已达成约定并登记寄出（合作进行中）。"""
    co = _agreed(client, runner)
    sid = _new_shipment(client, co["id"])["id"]
    _confirm(client, sid)
    r = client.post(
        f"/api/v1/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=_key()
    )
    assert r.status_code == 200, r.text
    return co


# ---------------- 拍摄包 ----------------


def test_brief_versions_immutable_and_locked_to_round(client, runner, fake_ai):
    co = _in_progress(client, runner)
    p = _prod(client, co["id"])
    assert p["content_language"] == "en" and p["brief_versions"] == [] and p["can_start_round"] is True

    v1 = _gen(client, co["id"], video_length_s=45, extra="突出便携")
    assert v1["version_no"] == 1 and v1["source"] == "ai" and v1["content_language"] == "en"
    assert v1["body_zh"] and "确认视频时长" in v1["cautions"]
    payload = json.dumps(fake_ai[0]["payload"], ensure_ascii=False)
    assert "突出便携" in payload and '"video_length_s": 45' in payload
    for secret in SECRETS:  # 已建寄样单（含收件信息），拍摄包请求里不能出现
        assert secret not in payload

    # 手动修改 → 新版本，旧版本不变
    same = client.post(
        f"/api/v1/collaborations/{co['id']}/brief-versions",
        json={
            "based_on_version_id": v1["id"],
            "title": v1["title"],
            "body": v1["body"],
            "body_zh": v1["body_zh"],
        },
    )
    assert same.status_code == 422 and same.json()["error"]["code"] == "no_change"
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/brief-versions",
        json={
            "based_on_version_id": v1["id"],
            "title": "Edited",
            "body": v1["body"] + "\nKeep it under 40s.",
        },
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version_no"] == 2 and v2["source"] == "manual" and v2["based_on_version_id"] == v1["id"]

    # 确认 v2 → 第 1 轮开始并锁定
    r = client.post(f"/api/v1/collaborations/{co['id']}/brief/confirm", json={"brief_version_id": v2["id"]})
    assert r.status_code == 200, r.text
    rnd = r.json()
    assert (
        rnd["round_no"] == 1 and rnd["status"] == "awaiting_video" and rnd["brief_version"]["id"] == v2["id"]
    )
    assert rnd["can_upload"] is True

    # 本轮锁定：不能换成 v1；再生成 v3 也不影响本轮
    r = client.post(f"/api/v1/collaborations/{co['id']}/brief/confirm", json={"brief_version_id": v1["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "brief_locked"
    _gen(client, co["id"])
    p = _prod(client, co["id"])
    assert [v["version_no"] for v in p["brief_versions"]] == [3, 2, 1]
    assert p["brief_versions"][1]["locked_round_nos"] == [1]
    assert p["rounds"][0]["brief_version"]["version_no"] == 2
    assert p["can_start_round"] is False and "第 1 轮" in p["start_round_blocker"]

    # 合作的下一步与历史
    d = client.get(f"/api/v1/collaborations/{co['id']}").json()
    assert d["next_step"] == "等待达人交第 1 轮视频"
    assert d["production"]["round_status"] == "awaiting_video"
    kinds = [e["kind"] for e in d["events"]]
    assert "brief_generated" in kinds and "round_started" in kinds and "brief_confirmed" in kinds


def test_production_requires_agreement_and_llm(client, runner, monkeypatch):
    cm_id, run_id, by = _setup(client)
    co = _keep_and_collab(client, cm_id, run_id, by["kitchen_kate"])
    r = client.post(f"/api/v1/collaborations/{co['id']}/brief-versions/generate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] in ("not_active", "llm_not_configured")
    p = _prod(client, co["id"])
    assert p["can_start_round"] is False and "约定" in p["start_round_blocker"]
    co2 = _agreed(client, runner)
    r = client.post(f"/api/v1/collaborations/{co2['id']}/brief-versions/generate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "llm_not_configured"

    def boom(system, payload):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(ai, "brief_model", boom)
    r = client.post(f"/api/v1/collaborations/{co2['id']}/brief-versions/generate", json={})
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_failed"
    assert _count("SELECT COUNT(*) FROM content_brief_versions") == 0
    r = client.post(f"/api/v1/collaborations/{co2['id']}/brief-versions/generate", json={"language": "xx"})
    assert r.status_code == 422


# ---------------- 视频与审核 ----------------


def test_v1_timecode_feedback_v2_accepted_counts_one(client, runner, fake_ai):
    co = _in_progress(client, runner)
    # 没确认拍摄包不能上传
    r = client.post(f"/api/v1/collaborations/{co['id']}/rounds", json={})
    assert r.status_code == 201
    rid = r.json()["id"]
    again = client.post(f"/api/v1/collaborations/{co['id']}/rounds", json={})
    assert again.status_code == 200 and again.json()["id"] == rid  # 重复点击不产生新轮次
    r = _upload(client, rid, V1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "brief_required"

    rnd = _confirmed_round(client, co["id"])
    assert rnd["id"] == rid and rnd["round_no"] == 1

    assert _upload(client, rid, V1, name="x.avi", ctype="video/x-msvideo").status_code == 415
    assert _upload(client, rid, b"").status_code == 422

    r1 = _upload(client, rid, V1, name="Kate 初稿.mp4")
    assert r1.status_code == 201, r1.text
    v1 = r1.json()
    assert v1["label"] == "V1" and v1["status"] == "in_review" and v1["original_name"] == "Kate 初稿.mp4"
    assert v1["size_bytes"] == len(V1)
    # 同一文件重复上传：返回同一版本，不产生第二个视频
    r1b = _upload(client, rid, V1, name="Kate 初稿 (1).mp4")
    assert r1b.status_code == 200 and r1b.json()["id"] == v1["id"]
    assert r1b.headers.get("Idempotent-Replayed") == "true"
    assert _count("SELECT COUNT(*) FROM video_versions") == 1
    # 审核中不能传新版本
    r = _upload(client, rid, V2)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_uploadable"

    # 要求返修至少 1 条反馈
    r = client.post(f"/api/v1/video-versions/{v1['id']}/review", json={"decision": "changes_requested"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "feedback_required"
    fb = client.post(
        f"/api/v1/video-versions/{v1['id']}/feedback",
        json={"timecode_ms": 12345, "category": "product", "severity": "must", "body": "产品 logo 被手挡住"},
    )
    assert fb.status_code == 201, fb.text
    assert fb.json()["timecode_ms"] == 12345
    extra = client.post(
        f"/api/v1/video-versions/{v1['id']}/feedback", json={"timecode_ms": 3000, "body": "开头太慢"}
    ).json()
    tmp = client.post(
        f"/api/v1/video-versions/{v1['id']}/feedback", json={"timecode_ms": 500, "body": "删掉"}
    ).json()
    assert client.delete(f"/api/v1/feedback-items/{tmp['id']}").status_code == 204
    view = client.get(f"/api/v1/video-versions/{v1['id']}").json()
    assert [f["timecode_ms"] for f in view["feedback"]] == [3000, 12345]  # 按时间码排序

    # 反馈消息：带时间码，不含收件信息
    m = client.post(f"/api/v1/video-versions/{v1['id']}/feedback-message", json={})
    assert m.status_code == 200, m.text
    assert m.json()["language"] == "en" and "00:12" in m.json()["message"] and "00:03" in m.json()["message"]
    fpayload = json.dumps(fake_ai[-1]["payload"], ensure_ascii=False)
    assert "产品 logo 被手挡住" in fpayload
    for secret in SECRETS:
        assert secret not in fpayload

    r = client.post(
        f"/api/v1/video-versions/{v1['id']}/review",
        json={
            "decision": "changes_requested",
            "summary": "整体不错",
            "message": m.json()["message"],
            "message_language": "en",
        },
    )
    assert r.status_code == 200, r.text
    rnd = r.json()
    assert rnd["status"] == "revision_requested" and rnd["counted"] is False
    v1v = rnd["videos"][0]
    assert v1v["status"] == "changes_requested" and all(f["submitted"] for f in v1v["feedback"])
    assert v1v["review"]["message"].startswith("Hi!")
    # 提交后反馈不可改
    assert client.delete(f"/api/v1/feedback-items/{extra['id']}").status_code == 409
    r = client.post(f"/api/v1/video-versions/{v1['id']}/feedback", json={"timecode_ms": 1, "body": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_reviewable"
    d = client.get(f"/api/v1/collaborations/{co['id']}").json()
    assert d["next_step"] == "等待达人交第 1 轮返修版本"
    assert d["follow_up"]["suggest_follow_up_draft"] is True

    # V2 → 验收 → 本轮计 1 条
    r2 = _upload(client, rid, V2, name="Kate v2.mp4")
    assert r2.status_code == 201
    v2 = r2.json()
    assert v2["label"] == "V2"
    assert client.get(f"/api/v1/collaborations/{co['id']}").json()["next_step"] == "审核第 1 轮视频"
    r = client.post(f"/api/v1/video-versions/{v2['id']}/review", json={"decision": "accepted"})
    assert r.status_code == 200, r.text
    rnd = r.json()
    assert rnd["status"] == "accepted" and rnd["counted"] is True and rnd["accepted_version_id"] == v2["id"]
    assert rnd["brief_version"]["version_no"] == 1  # 本轮锁定的拍摄包没有变
    p = _prod(client, co["id"])
    assert p["accepted_videos"] == 1  # V1 + V2 只算 1 条
    # 已验收：不能再审核、不能再上传
    r = client.post(f"/api/v1/video-versions/{v2['id']}/review", json={"decision": "accepted"})
    assert r.status_code == 409
    r = _upload(client, rid, V1 + b"new")
    assert r.status_code == 409 and r.json()["error"]["code"] == "round_closed"

    d = client.get(f"/api/v1/collaborations/{co['id']}").json()
    assert d["production"]["accepted_videos"] == 1 and "completed" in d["allowed_transitions"]
    assert d["next_step"] == "第 1 轮已验收，开始下一轮"  # 约定 2 条，才交 1 条
    assert any("本轮计 1 条" in e["note"] for e in d["events"])


def test_second_round_and_completion(client, runner, fake_ai):
    co = _in_progress(client, runner)
    # 没有验收的视频不能完成
    r = client.post(f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "completed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "no_accepted_video"

    rnd1 = _confirmed_round(client, co["id"])
    v = _upload(client, rnd1["id"], V1).json()
    client.post(f"/api/v1/video-versions/{v['id']}/review", json={"decision": "accepted"})

    # 第 2 轮：沿用 v1 拍摄包也可以（同一版本锁定到两轮）
    b1 = _prod(client, co["id"])["brief_versions"][0]
    r = client.post(f"/api/v1/collaborations/{co['id']}/brief/confirm", json={"brief_version_id": b1["id"]})
    assert r.status_code == 200
    rnd2 = r.json()
    assert rnd2["round_no"] == 2
    assert _prod(client, co["id"])["brief_versions"][0]["locked_round_nos"] == [1, 2]
    # 同一文件用于第 2 轮：新版本，但文件只存一份
    v2 = _upload(client, rnd2["id"], V1)
    assert v2.status_code == 201 and v2.json()["label"] == "V1"
    assert _count("SELECT COUNT(*) FROM video_assets") == 1
    assert _count("SELECT COUNT(*) FROM video_versions") == 2

    # 第 2 轮在审核中：不能完成、不能开第 3 轮
    r = client.post(f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "completed"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "round_open"
    r = client.post(f"/api/v1/collaborations/{co['id']}/rounds", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "round_open"

    client.post(f"/api/v1/video-versions/{v2.json()['id']}/review", json={"decision": "accepted"})
    d = client.get(f"/api/v1/collaborations/{co['id']}").json()
    assert d["production"]["accepted_videos"] == 2
    assert d["next_step"] == "约定的视频已全部验收，可以完成合作" and d["follow_up"] is None

    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "completed", "note": "两条都发布了"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    r = client.post(f"/api/v1/collaborations/{co['id']}/brief-versions/generate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_active"


def test_closing_collaboration_cancels_open_round(client, runner, fake_ai):
    co = _in_progress(client, runner)
    rnd = _confirmed_round(client, co["id"])
    r = client.post(
        f"/api/v1/collaborations/{co['id']}/transitions", json={"to": "closed", "closed_reason": "no_reply"}
    )
    assert r.status_code == 200, r.text
    p = _prod(client, co["id"])
    assert p["rounds"][0]["id"] == rnd["id"] and p["rounds"][0]["status"] == "cancelled"
    assert p["accepted_videos"] == 0


def test_cancel_round_then_start_new(client, runner, fake_ai):
    co = _in_progress(client, runner)
    rnd = _confirmed_round(client, co["id"])
    r = client.post(f"/api/v1/rounds/{rnd['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/rounds/{rnd['id']}/cancel").status_code == 409
    r = client.post(f"/api/v1/collaborations/{co['id']}/rounds", json={})
    assert r.status_code == 201 and r.json()["round_no"] == 2


# ---------------- 播放地址 ----------------


def test_media_needs_valid_signature_and_supports_range(client, runner, fake_ai):
    co = _in_progress(client, runner)
    rnd = _confirmed_round(client, co["id"])
    v = _upload(client, rnd["id"], V1).json()
    url = v["media_url"]
    assert url.startswith("/api/v1/media/") and "sig=" in url

    bare = {"Authorization": ""}
    r = client.get(url, headers=bare)
    assert r.status_code == 200 and r.content == V1 and r.headers["content-type"] == "video/mp4"
    r = client.get(url, headers={**bare, "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == V1[:100]

    asset = url.split("/media/")[1].split("?")[0]
    assert client.get(f"/api/v1/media/{asset}", headers=bare).status_code == 401
    assert client.get(url[:-4] + "0000", headers=bare).status_code == 401
    exp = int(time.time()) - 10
    expired = f"/api/v1/media/{asset}?exp={exp}&sig={media._sig(asset, exp)}"
    assert client.get(expired, headers=bare).status_code == 401
    # 签名只对 media 路径有效，不能拿来访问其他接口
    q = url.split("?")[1]
    assert client.get(f"/api/v1/collaborations?{q}", headers=bare).status_code == 401
    assert client.post(url, headers=bare).status_code == 401
