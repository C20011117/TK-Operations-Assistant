"""M4 验收：拍摄包与视频审核，逐条检查完成标准。

用法（仓库根目录）：cd backend && uv run python ../scripts/m4_acceptance.py
- 全新的临时数据目录；候选用人工导入（不调用 FastMoss）；拍摄包和反馈消息用假模型（不花大模型额度）。
- 后端在本进程内运行，不读写 Windows 凭据管理器（不会覆盖已保存的 API Key）。
- 视频文件是脚本生成的字节内容，只用来验证保存、去重和播放地址，不需要真的能播放。

完成标准：目标语言拍摄包确认后，版本锁定在本轮；V1 反馈精确到时间码；V2 验收后本轮计 1 条。
"""

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

tmp = tempfile.mkdtemp(prefix="tkws-m4-")
os.environ["TKWS_DATA_DIR"] = os.path.join(tmp, "TKWorkspace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from tk_workspace.api.main import create_app  # noqa: E402
from tk_workspace.config import get_settings  # noqa: E402
from tk_workspace.modules.production import ai  # noqa: E402
from tk_workspace.modules.production.ai import BriefOut, FeedbackOut  # noqa: E402
from tk_workspace.platform.db.migrate import upgrade_to_head  # noqa: E402
from tk_workspace.platform.jobs.runner import JobRunner  # noqa: E402

TOKEN = "m4-accept-token-0123456789"  # noqa: S105 — 仅本脚本的临时令牌
results: list[bool] = []
llm_payloads: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def key() -> dict:
    return {"Idempotency-Key": f"m4-{uuid.uuid4()}"}


def fake_brief(system, payload):
    llm_payloads.append(json.dumps(payload, ensure_ascii=False))
    lang = payload["language"].split(" ")[0]
    return (
        BriefOut(
            title=f"[{lang}] Portable blender reel",
            body=f"[{lang}] Hook (0-3s)... Shot list... Add #ad. CTA: tap the cart.",
            body_zh="钩子（0-3 秒）…… 分镜…… 加 #ad。行动号召：点购物车。",
            cautions=[],
        ),
        "fake",
    )


def fake_feedback(system, payload):
    llm_payloads.append(json.dumps(payload, ensure_ascii=False))
    lines = "\n".join(f"- {f['timecode']} {f['text']}" for f in payload["feedback"])
    return FeedbackOut(message=f"Hi! Please change:\n{lines}", message_zh=f"请修改：\n{lines}"), "fake"


ai.brief_model = fake_brief
ai.feedback_model = fake_feedback

upgrade_to_head()
runner = JobRunner(workers=1, poll_seconds=0.05)
runner.start()
V1 = b"\x00\x00\x00\x18ftypmp42" + os.urandom(200_000)
V2 = b"\x00\x00\x00\x18ftypmp42" + os.urandom(250_000)

try:
    with TestClient(create_app(get_settings(), token=TOKEN), base_url="http://127.0.0.1:8765") as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        B = "/api/v1"

        def up(round_id: str, data: bytes, name: str):
            return c.post(
                f"{B}/rounds/{round_id}/videos",
                content=data,
                headers={"Content-Type": "video/mp4", "X-Filename": quote(name)},
            )

        # ---------- 准备：德国任务（验证目标语言 = 德语）+ 人工导入候选 + 记录约定 ----------
        p = c.post(f"{B}/products", json={"sku": "JC-01", "name": "便携榨汁杯"}).json()
        facts = {"summary": "便携榨汁杯", "selling_points": ["便携"], "use_scenarios": ["办公室"], "forbidden_claims": []}
        terms = [{"market_code": "DE", "price_status": "known", "price_amount": "24.99", "sample_policy": "free"}]
        c.put(f"{B}/products/{p['id']}/draft", json={"facts": facts, "market_terms": terms}).raise_for_status()
        c.post(f"{B}/products/{p['id']}/draft/confirm").raise_for_status()
        camp = c.post(
            f"{B}/campaigns",
            json={"product_id": p["id"], "name": "榨汁杯 DE", "goal": "sales", "collaboration_type": "free_sample",
                  "market_code": "DE", "market": {}},
        ).json()
        cm = camp["markets"][0]["id"]
        c.put(f"{B}/campaign-markets/{cm}", json={"target_list_size": 5, "cost_cap_credits": 5}).raise_for_status()
        crit = [{"field_key": "follower_count", "operator": "between", "expected": {"min": 1000, "max": 900000},
                 "hardness": "hard"}]
        c.put(f"{B}/campaign-markets/{cm}/criteria", json={"criteria": crit, "search": {"keywords": ["mixer"]}}
              ).raise_for_status()
        c.post(f"{B}/campaign-markets/{cm}/confirm").raise_for_status()
        csv_text = "用户名,昵称,粉丝数,近28天GMV,币种\n@kuechen_klara,Klara,82000,1500,EUR\n"
        run = c.post(f"{B}/campaign-markets/{cm}/manual-import", json={"csv": csv_text}, headers=key()).json()
        for _ in range(200):
            if c.get(f"{B}/matching-runs/{run['id']}").json()["status"] not in ("queued", "running"):
                break
            time.sleep(0.1)
        card = c.get(f"{B}/matching-runs/{run['id']}/recommendations").json()["cards"][0]
        creator = card["creator"]["id"]
        c.post(
            f"{B}/campaign-markets/{cm}/creator-decisions",
            json={"creator_id": creator, "decision": "keep", "run_id": run["id"], "evaluation_id": card["evaluation_id"]},
        ).raise_for_status()
        co = c.post(f"{B}/campaign-markets/{cm}/collaborations", json={"creator_id": creator}).json()
        r = c.post(f"{B}/collaborations/{co['id']}/brief-versions/generate", json={})
        check("⑦ 没有记录双方约定不能生成拍摄包", r.status_code == 409, r.json()["error"]["code"])
        c.post(
            f"{B}/collaborations/{co['id']}/confirm-agreement",
            json={"agreed_via": "tiktok_message", "agreed_on": "2026-09-28", "agreed_video_count": 1,
                  "terms_note": "免费寄样，一条新视频"},
        ).raise_for_status()
        cid = co["id"]

        # ---------- 步骤 7：目标语言拍摄包，编辑后确认，本轮锁定 ----------
        v1 = c.post(f"{B}/collaborations/{cid}/brief-versions/generate", json={}).json()
        check("⑦ 拍摄包默认用站点语言（德国站 = 德语），附中文对照",
              v1["content_language"] == "de" and v1["title"].startswith("[de]") and bool(v1["body_zh"]))
        check("⑦ 发给模型的内容不含收件信息字段", all("recipient" not in pl and "address" not in pl for pl in llm_payloads))
        v2 = c.post(
            f"{B}/collaborations/{cid}/brief-versions",
            json={"based_on_version_id": v1["id"], "title": v1["title"], "body": v1["body"] + "\nMax. 30 Sekunden.",
                  "body_zh": v1["body_zh"]},
        ).json()
        check("⑦ 修改产生新版本，旧版本不变", v2["version_no"] == 2 and v2["based_on_version_id"] == v1["id"])
        rnd = c.post(f"{B}/collaborations/{cid}/brief/confirm", json={"brief_version_id": v2["id"]}).json()
        rid = rnd["id"]
        check("⑦ 确认后开始第 1 轮，锁定 v2", rnd["round_no"] == 1 and rnd["brief_version"]["version_no"] == 2)
        r = c.post(f"{B}/collaborations/{cid}/brief/confirm", json={"brief_version_id": v1["id"]})
        check("⑦ 本轮锁定后不能换成别的版本", r.status_code == 409 and r.json()["error"]["code"] == "brief_locked")
        c.post(f"{B}/collaborations/{cid}/brief-versions/generate", json={}).raise_for_status()
        prod = c.get(f"{B}/collaborations/{cid}/production").json()
        check("⑦ 之后再生成 v3，本轮仍是 v2；旧版本都能查到",
              prod["rounds"][0]["brief_version"]["version_no"] == 2
              and [x["version_no"] for x in prod["brief_versions"]] == [3, 2, 1])

        # ---------- 步骤 8：V1 → 时间码反馈 → V2 → 验收 ----------
        r1 = up(rid, V1, "Klara Entwurf.mp4")
        vid1 = r1.json()["id"]
        check("⑧ 上传 V1", r1.status_code == 201 and r1.json()["label"] == "V1")
        r1b = up(rid, V1, "Klara Entwurf (Kopie).mp4")
        check("⑧ 同一文件重复上传不产生第二个视频", r1b.status_code == 200 and r1b.json()["id"] == vid1)
        files = list((get_settings().files_dir / "videos").rglob("*.mp4"))
        check("⑧ 视频保存在本机数据目录，按内容只存一份", len(files) == 1 and files[0].stat().st_size == len(V1))
        url = r1.json()["media_url"]
        rg = c.get(url, headers={"Authorization": "", "Range": "bytes=0-1023"})
        check("⑧ 播放地址（签名）可用且支持拖动（Range）", rg.status_code == 206 and rg.content == V1[:1024])
        check("⑧ 没有签名不能读取视频", c.get(url.split("?")[0], headers={"Authorization": ""}).status_code == 401)

        c.post(f"{B}/video-versions/{vid1}/feedback",
               json={"timecode_ms": 12_345, "category": "product", "severity": "must", "body": "Logo 被挡住"}
               ).raise_for_status()
        c.post(f"{B}/video-versions/{vid1}/feedback",
               json={"timecode_ms": 3_000, "category": "script", "severity": "should", "body": "开头再快一点"}
               ).raise_for_status()
        fb = c.get(f"{B}/video-versions/{vid1}").json()["feedback"]
        check("⑧ V1 反馈精确到时间码（毫秒保存，按时间排序）", [f["timecode_ms"] for f in fb] == [3000, 12345])
        m = c.post(f"{B}/video-versions/{vid1}/feedback-message", json={}).json()
        check("⑧ 反馈消息用拍摄包语言，逐条带时间码",
              m["language"] == "de" and "00:12" in m["message"] and "00:03" in m["message"])
        rv = c.post(f"{B}/video-versions/{vid1}/review",
                    json={"decision": "changes_requested", "message": m["message"], "message_language": "de"}).json()
        check("⑧ 要求返修后本轮不计数", rv["status"] == "revision_requested" and rv["counted"] is False)
        r = c.post(f"{B}/video-versions/{vid1}/feedback", json={"timecode_ms": 1, "body": "x"})
        check("⑧ 提交后的反馈不能再改", r.status_code == 409)

        r2 = up(rid, V2, "Klara v2.mp4")
        vid2 = r2.json()["id"]
        check("⑧ 上传 V2", r2.status_code == 201 and r2.json()["label"] == "V2")
        acc = c.post(f"{B}/video-versions/{vid2}/review", json={"decision": "accepted"}).json()
        prod = c.get(f"{B}/collaborations/{cid}/production").json()
        check("⑧ V2 验收后本轮计 1 条（V1、V2 只算 1 条）",
              acc["status"] == "accepted" and acc["counted"] and prod["accepted_videos"] == 1)
        check("⑧ 验收后本轮拍摄包仍是 v2", acc["brief_version"]["version_no"] == 2)
        r = up(rid, V1 + b"x", "late.mp4")
        check("⑧ 已验收的轮次不能再上传", r.status_code == 409 and r.json()["error"]["code"] == "round_closed")
        old = c.get(f"{B}/video-versions/{vid1}").json()
        check("⑧ 旧视频版本、旧反馈、发给达人的消息都能查到",
              len(old["feedback"]) == 2 and old["review"]["message"] == m["message"])
        d = c.get(f"{B}/collaborations/{cid}").json()
        check("⑧ 约定 1 条已交齐：可以完成合作，不再提醒跟进",
              "completed" in d["allowed_transitions"] and d["follow_up"] is None)
        done = c.post(f"{B}/collaborations/{cid}/transitions", json={"to": "completed"})
        check("⑧ 完成合作", done.status_code == 200 and done.json()["status"] == "completed")
        check("⑧ 合作历史记录了拍摄包确认、V1、返修、V2、验收",
              {"brief_confirmed", "video_uploaded", "revision_requested", "video_accepted"}
              <= {e["kind"] for e in done.json()["events"]})
finally:
    runner.stop()

print(f"\n{sum(results)}/{len(results)} 项通过；临时数据目录 {tmp}")
sys.exit(0 if all(results) else 1)
