import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Badge, Button, Card, ErrorText, Field, Input, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { BriefVersionView, CollaborationDetail, ProductionView, RoundView } from "@/lib/api/types";
import { copyText } from "@/lib/clipboard";
import { fmtTime } from "@/lib/labels";

import { langName, roundStatusLabels, roundTone, videoStatusLabels } from "./labels";
import { UploadButton } from "./UploadButton";

function useRefresh(collabId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["production", collabId] });
    qc.invalidateQueries({ queryKey: ["collaboration", collabId] });
    qc.invalidateQueries({ queryKey: ["collaborations"] });
  };
}

function CopyButton({ text, label = "复制正文" }: { text: string; label?: string }) {
  const [done, setDone] = useState("");
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="secondary"
        onClick={async () => {
          setDone((await copyText(text)) ? "已复制" : "复制失败，请手动选中复制");
          window.setTimeout(() => setDone(""), 2500);
        }}
      >
        {label}
      </Button>
      {done && <span className="text-xs text-emerald-700">{done}</span>}
    </span>
  );
}

/** 拍摄包：生成 / 查看版本 / 修改为新版本 / 确认用于本轮。 */
function BriefSection({ c, p }: { c: CollaborationDetail; p: ProductionView }) {
  const refresh = useRefresh(c.id);
  const [selected, setSelected] = useState<string | null>(null);
  const [language, setLanguage] = useState(p.content_language);
  const [length, setLength] = useState("30");
  const [extra, setExtra] = useState("");
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [bodyZh, setBodyZh] = useState("");

  const versions = p.brief_versions;
  const v: BriefVersionView | undefined = versions.find((x) => x.id === selected) ?? versions[0];
  const open = p.rounds.find((r) => ["briefing", "awaiting_video", "in_review", "revision_requested"].includes(r.status));
  const nextRoundNo = (p.rounds[0]?.round_no ?? 0) + (open ? 0 : 1);
  const canConfirm = c.can_create_shipment && (!open || open.status === "briefing");
  const writable = c.can_create_shipment;

  useEffect(() => {
    if (v && !editing) {
      setTitle(v.title);
      setBody(v.body);
      setBodyZh(v.body_zh);
    }
  }, [v, editing]);

  const gen = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/brief-versions/generate", {
          params: { path: { collab_id: c.id } },
          body: { language, video_length_s: Number(length) || 30, extra },
        }),
      ),
    onSuccess: (nv) => {
      setSelected(nv.id);
      refresh();
    },
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/brief-versions", {
          params: { path: { collab_id: c.id } },
          body: { based_on_version_id: v!.id, title, body, body_zh: bodyZh },
        }),
      ),
    onSuccess: (nv) => {
      setEditing(false);
      setSelected(nv.id);
      refresh();
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/brief/confirm", {
          params: { path: { collab_id: c.id } },
          body: { brief_version_id: v!.id },
        }),
      ),
    onSuccess: refresh,
  });
  const err = gen.error ?? save.error ?? confirm.error;

  return (
    <div className="space-y-4">
      {writable && (
        <div className="rounded-md bg-slate-50 p-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="拍摄包语言">
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                {p.content_languages.map((l) => (
                  <option key={l} value={l}>
                    {langName(l)}
                  </option>
                ))}
                {!p.content_languages.includes("en") && <option value="en">英语</option>}
              </Select>
            </Field>
            <Field label="建议时长">
              <Select value={length} onChange={(e) => setLength(e.target.value)}>
                {["15", "30", "45", "60", "90"].map((s) => (
                  <option key={s} value={s}>
                    {s} 秒
                  </option>
                ))}
              </Select>
            </Field>
            <div className="col-span-2">
              <Field label="补充要求（可选）">
                <Input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="例如：突出一键清洗；户外场景" />
              </Field>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button disabled={gen.isPending} onClick={() => gen.mutate()}>
              {gen.isPending ? "AI 生成中…（约 30–90 秒）" : versions.length ? "重新生成一版" : "AI 生成拍摄包"}
            </Button>
            <span className="text-xs text-slate-500">依据产品资料、站点条款、双方约定和达人公开数据；不发送收件信息。</span>
          </div>
        </div>
      )}

      {v && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Select className="w-auto" value={v.id} onChange={(e) => (setEditing(false), setSelected(e.target.value))}>
              {versions.map((x) => (
                <option key={x.id} value={x.id}>
                  v{x.version_no} · {x.source === "ai" ? "AI 生成" : "手动修改"} · {fmtTime(x.created_at)}
                  {x.locked_round_nos.length ? ` · 已用于第 ${x.locked_round_nos.join("、")} 轮` : ""}
                </option>
              ))}
            </Select>
            <Badge tone="blue">{langName(v.content_language)}</Badge>
            {v.locked_round_nos.length > 0 && <Badge tone="green">已锁定：第 {v.locked_round_nos.join("、")} 轮</Badge>}
          </div>
          {v.cautions.length > 0 && !editing && (
            <ul className="list-disc space-y-0.5 rounded-md bg-amber-50 py-2 pl-7 pr-3 text-sm text-amber-900">
              {v.cautions.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
          {editing ? (
            <div className="space-y-3">
              <Field label="标题">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
              <Field label={`正文（${langName(v.content_language)}，发给达人的版本）`}>
                <Textarea rows={16} value={body} onChange={(e) => setBody(e.target.value)} />
              </Field>
              <Field label="中文对照（自己核对用，可以不改）">
                <Textarea rows={8} value={bodyZh} onChange={(e) => setBodyZh(e.target.value)} />
              </Field>
              <div className="flex gap-2">
                <Button disabled={save.isPending || !title.trim() || !body.trim()} onClick={() => save.mutate()}>
                  保存为 v{(versions[0]?.version_no ?? 0) + 1}
                </Button>
                <Button variant="ghost" onClick={() => setEditing(false)}>
                  取消
                </Button>
              </div>
              <p className="text-xs text-slate-500">保存会产生新版本，原来的版本不变（已锁定到某一轮的版本也不会被改动）。</p>
            </div>
          ) : (
            <>
              <h4 className="font-medium">{v.title}</h4>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-slate-200 p-3 font-sans text-sm leading-relaxed">
                {v.body}
              </pre>
              <details className="rounded-md bg-slate-50 p-3 text-sm">
                <summary className="cursor-pointer text-slate-600">中文对照</summary>
                <pre className="mt-2 whitespace-pre-wrap font-sans text-slate-700">{v.body_zh || "（没有中文对照）"}</pre>
              </details>
              <div className="flex flex-wrap items-center gap-2">
                <CopyButton text={`${v.title}\n\n${v.body}`} />
                {writable && (
                  <Button variant="secondary" onClick={() => setEditing(true)}>
                    修改
                  </Button>
                )}
                {canConfirm && (
                  <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>
                    确认 v{v.version_no}，用于第 {nextRoundNo} 轮
                  </Button>
                )}
              </div>
              {canConfirm && (
                <p className="text-xs text-slate-500">
                  确认后本轮的拍摄包就固定为这个版本；复制正文发给达人，等对方交视频后在下面上传。
                </p>
              )}
            </>
          )}
        </div>
      )}
      {err && <ErrorText>{errorMessage(err)}</ErrorText>}
    </div>
  );
}

function RoundRow({ r, collabId, writable }: { r: RoundView; collabId: string; writable: boolean }) {
  const refresh = useRefresh(collabId);
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/rounds/{round_id}/cancel", { params: { path: { round_id: r.id } } })),
    onSuccess: refresh,
  });
  const latest = r.videos[0];
  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">第 {r.round_no} 轮</span>
        <Badge tone={roundTone[r.status]}>{roundStatusLabels[r.status]}</Badge>
        {r.counted && <Badge tone="green">计 1 条</Badge>}
        {r.brief_version && <span className="text-xs text-slate-500">拍摄包 v{r.brief_version.version_no}</span>}
        <span className="ml-auto flex flex-wrap items-center gap-2">
          {r.videos.length > 0 && (
            <Link to={`/rounds/${r.id}`}>
              <Button variant={r.status === "in_review" ? "primary" : "secondary"}>
                {r.status === "in_review" ? `审核 ${latest?.label ?? ""}` : "查看视频与反馈"}
              </Button>
            </Link>
          )}
          {r.can_upload && writable && <UploadButton round={r} onDone={refresh} />}
          {writable && ["briefing", "awaiting_video", "revision_requested"].includes(r.status) && (
            <Button
              variant="ghost"
              disabled={cancel.isPending}
              onClick={() => window.confirm(`取消第 ${r.round_no} 轮？取消后不计数。`) && cancel.mutate()}
            >
              取消本轮
            </Button>
          )}
        </span>
      </div>
      {r.videos.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm">
          {r.videos.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{v.label}</span>
              <span className="text-slate-600">{v.original_name}</span>
              <Badge tone={v.status === "accepted" ? "green" : v.status === "in_review" ? "amber" : "slate"}>
                {videoStatusLabels[v.status]}
              </Badge>
              {v.feedback.length > 0 && <span className="text-xs text-slate-500">{v.feedback.length} 条反馈</span>}
              <span className="text-xs text-slate-400">{fmtTime(v.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
      {r.status === "awaiting_video" && r.videos.length === 0 && (
        <p className="mt-2 text-xs text-slate-500">等达人交初稿（V1）。收到后点“上传视频”。</p>
      )}
      {cancel.error && <ErrorText>{errorMessage(cancel.error)}</ErrorText>}
    </div>
  );
}

/** 合作详情页：拍摄包 + 各轮视频。 */
export function ProductionCard({ c }: { c: CollaborationDetail }) {
  const ref = useRef<HTMLDivElement>(null);
  const q = useQuery({
    queryKey: ["production", c.id],
    queryFn: () =>
      unwrap(api.GET("/api/v1/collaborations/{collab_id}/production", { params: { path: { collab_id: c.id } } })),
  });
  if (["planned", "contacting", "negotiating"].includes(c.status)) return null;
  const p = q.data;
  const writable = c.can_create_shipment;
  return (
    <div ref={ref}>
      <Card
        title="拍摄与视频"
        actions={
          p && (
            <span className="text-sm text-slate-600">
              已验收 <span className="font-semibold text-slate-900">{p.accepted_videos}</span>
              {p.agreed_video_count ? ` / 约定 ${p.agreed_video_count}` : ""} 条
            </span>
          )
        }
      >
        {q.error && <ErrorText>{errorMessage(q.error)}</ErrorText>}
        {p && (
          <div className="space-y-5">
            <section>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">拍摄包</h3>
              {!writable && p.brief_versions.length === 0 ? (
                <p className="text-sm text-slate-500">没有拍摄包。</p>
              ) : (
                <BriefSection c={c} p={p} />
              )}
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">拍摄轮次（1 轮 = 1 条新视频，返修不另算）</h3>
              {p.rounds.length === 0 ? (
                <p className="text-sm text-slate-500">
                  还没有开始。确认一个拍摄包版本后会自动开始第 1 轮。
                </p>
              ) : (
                <div className="space-y-2">
                  {p.rounds.map((r) => (
                    <RoundRow key={r.id} r={r} collabId={c.id} writable={writable} />
                  ))}
                </div>
              )}
              {p.start_round_blocker && writable && p.rounds.length > 0 && (
                <p className="mt-2 text-xs text-slate-500">{p.start_round_blocker}，结束后才能开始下一轮。</p>
              )}
            </section>
          </div>
        )}
      </Card>
    </div>
  );
}
