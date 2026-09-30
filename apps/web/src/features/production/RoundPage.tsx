import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { Badge, Button, Card, Empty, ErrorText, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { FeedbackIn, FeedbackMessageView, FeedbackView, RoundView, VideoVersionView } from "@/lib/api/types";
import { copyText } from "@/lib/clipboard";
import { fmtTime } from "@/lib/labels";

import {
  categoryLabels,
  fmtSize,
  fmtTc,
  langName,
  LANG_LABELS,
  parseTc,
  roundStatusLabels,
  roundTone,
  videoStatusLabels,
} from "./labels";
import { UploadButton } from "./UploadButton";
import { mediaSrc } from "./upload";

type Category = NonNullable<FeedbackIn["category"]>;
type Severity = NonNullable<FeedbackIn["severity"]>;

function FeedbackList({
  items,
  onSeek,
  onDelete,
  duration,
}: {
  items: FeedbackView[];
  onSeek?: (ms: number) => void;
  onDelete?: (id: string) => void;
  duration?: number;
}) {
  if (items.length === 0) return <p className="text-sm text-slate-500">还没有反馈。</p>;
  return (
    <ul className="space-y-2">
      {items.map((f) => (
        <li key={f.id} className="rounded-md border border-slate-200 p-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="font-mono text-sky-700 hover:underline disabled:text-slate-700 disabled:no-underline"
              disabled={!onSeek}
              onClick={() => onSeek?.(f.timecode_ms)}
              title="跳到这个位置"
            >
              {fmtTc(f.timecode_ms)}
            </button>
            {duration !== undefined && duration > 0 && f.timecode_ms > duration * 1000 + 500 && (
              <Badge tone="red">超出视频长度</Badge>
            )}
            <Badge tone={f.severity === "must" ? "red" : "slate"}>{f.severity === "must" ? "必须改" : "建议改"}</Badge>
            <span className="text-xs text-slate-500">{categoryLabels[f.category]}</span>
            {onDelete && !f.submitted && (
              <button type="button" className="ml-auto text-xs text-slate-400 hover:text-red-600" onClick={() => onDelete(f.id)}>
                删除
              </button>
            )}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-slate-800">{f.body}</p>
        </li>
      ))}
    </ul>
  );
}

function Player({
  v,
  videoRef,
  onTime,
  feedback,
}: {
  v: VideoVersionView;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onTime: (ms: number, paused: boolean) => void;
  feedback: FeedbackView[];
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [duration, setDuration] = useState(0);
  useEffect(() => {
    setFailed(false);
    mediaSrc(v.media_url).then(setSrc);
    // 同一个视频的签名地址会变：只在切换视频时重新加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v.id]);
  const seek = (ms: number) => {
    const el = videoRef.current;
    if (el) {
      el.currentTime = ms / 1000;
      el.pause();
    }
  };
  return (
    <div className="space-y-2">
      {src && !failed && (
        <video
          key={v.id}
          ref={videoRef}
          src={src}
          controls
          preload="metadata"
          className="max-h-[70vh] w-full rounded-md bg-black"
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          onTimeUpdate={(e) => onTime(e.currentTarget.currentTime * 1000, e.currentTarget.paused)}
          onPause={(e) => onTime(e.currentTarget.currentTime * 1000, true)}
          onSeeked={(e) => onTime(e.currentTarget.currentTime * 1000, e.currentTarget.paused)}
          onError={() => setFailed(true)}
        />
      )}
      {failed && (
        <div className="rounded-md bg-amber-50 p-4 text-sm text-amber-900">
          这个视频在应用里播放不了（常见于部分 iPhone 拍的 HEVC 视频）。可以用电脑上的播放器打开原文件观看，
          再在右边手动输入时间码添加反馈。
        </div>
      )}
      {duration > 0 && feedback.length > 0 && (
        <div className="relative h-3 rounded bg-slate-200" title="反馈位置">
          {feedback.map((f) => (
            <button
              key={f.id}
              type="button"
              title={`${fmtTc(f.timecode_ms)} ${f.body}`}
              onClick={() => seek(f.timecode_ms)}
              className={`absolute top-0 h-3 w-1.5 -translate-x-1/2 rounded ${f.severity === "must" ? "bg-red-500" : "bg-slate-500"}`}
              style={{ left: `${Math.min(100, (f.timecode_ms / 1000 / duration) * 100)}%` }}
            />
          ))}
        </div>
      )}
      <p className="text-xs text-slate-500">
        {v.original_name} · {fmtSize(v.size_bytes)} · 上传于 {fmtTime(v.created_at)}
      </p>
    </div>
  );
}

function AddFeedback({ v, posMs, onAdded }: { v: VideoVersionView; posMs: number; onAdded: () => void }) {
  const [manual, setManual] = useState<string | null>(null);
  const [category, setCategory] = useState<Category>("visual");
  const [severity, setSeverity] = useState<Severity>("must");
  const [body, setBody] = useState("");
  const tcText = manual ?? fmtTc(posMs);
  const tc = parseTc(tcText);
  const add = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/video-versions/{vid}/feedback", {
          params: { path: { vid: v.id } },
          body: { timecode_ms: tc ?? 0, category, severity, body },
        }),
      ),
    onSuccess: () => {
      setBody("");
      setManual(null);
      onAdded();
    },
  });
  return (
    <div className="space-y-2 rounded-md bg-slate-50 p-3">
      <div className="grid grid-cols-3 gap-2">
        <Field label="时间码" hint={manual === null ? "跟随播放位置" : "手动输入"}>
          <Input
            value={tcText}
            onChange={(e) => setManual(e.target.value)}
            className={`font-mono ${tc === null ? "ring-1 ring-red-400" : ""}`}
            aria-label="时间码"
          />
        </Field>
        <Field label="类别">
          <Select value={category} onChange={(e) => setCategory(e.target.value as Category)}>
            {Object.entries(categoryLabels).map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="程度">
          <Select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
            <option value="must">必须改</option>
            <option value="should">建议改</option>
          </Select>
        </Field>
      </div>
      <Textarea
        rows={2}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="这个位置要怎么改（中文即可，发给达人时再生成目标语言）"
      />
      <div className="flex items-center gap-2">
        <Button disabled={add.isPending || tc === null || !body.trim()} onClick={() => add.mutate()}>
          在 {tc === null ? "?" : fmtTc(tc)} 添加反馈
        </Button>
        {manual !== null && (
          <Button variant="ghost" onClick={() => setManual(null)}>
            用播放位置
          </Button>
        )}
      </div>
      {tc === null && <p className="text-xs text-red-600">时间码格式：分:秒，例如 0:12.5 或 1:05</p>}
      {add.error && <ErrorText>{errorMessage(add.error)}</ErrorText>}
    </div>
  );
}

function ReviewPanel({ v, r, defaultLang, onDone }: { v: VideoVersionView; r: RoundView; defaultLang: string; onDone: () => void }) {
  const [summary, setSummary] = useState("");
  const [lang, setLang] = useState(defaultLang);
  const [msg, setMsg] = useState<FeedbackMessageView | null>(null);
  const [text, setText] = useState("");
  const [copied, setCopied] = useState("");
  const pending = v.feedback.filter((f) => !f.submitted);
  const gen = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/video-versions/{vid}/feedback-message", {
          params: { path: { vid: v.id } },
          body: { language: lang, extra: summary },
        }),
      ),
    onSuccess: (m) => {
      setMsg(m);
      setText(m.message);
    },
  });
  const review = useMutation({
    mutationFn: (decision: "changes_requested" | "accepted") =>
      unwrap(
        api.POST("/api/v1/video-versions/{vid}/review", {
          params: { path: { vid: v.id } },
          body: {
            decision,
            summary,
            message: decision === "changes_requested" && text ? text : null,
            message_language: decision === "changes_requested" && text ? (msg?.language ?? lang) : null,
          },
        }),
      ),
    onSuccess: onDone,
  });
  return (
    <Card title={`审核结论 · 第 ${r.round_no} 轮 ${v.label}`}>
      <div className="space-y-3">
        <Field label="总体评价（可选，中文）">
          <Input value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="例如：整体节奏很好，开头的产品特写很清楚" />
        </Field>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="发给达人的语言">
            <Select className="w-36" value={lang} onChange={(e) => setLang(e.target.value)}>
              {Object.entries(LANG_LABELS).map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </Select>
          </Field>
          <Button variant="secondary" disabled={gen.isPending || pending.length === 0} onClick={() => gen.mutate()}>
            {gen.isPending ? "生成中…" : "生成反馈消息"}
          </Button>
          <span className="pb-2 text-xs text-slate-500">把上面 {pending.length} 条反馈写成{langName(lang)}消息，逐条带时间码。</span>
        </div>
        {gen.error && <ErrorText>{errorMessage(gen.error)}</ErrorText>}
        {msg && (
          <div className="space-y-2">
            {msg.warnings.length > 0 && (
              <ul className="list-disc rounded-md bg-amber-50 py-2 pl-7 pr-3 text-sm text-amber-900">
                {msg.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            )}
            <Textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} />
            <details className="rounded-md bg-slate-50 p-3 text-sm">
              <summary className="cursor-pointer text-slate-600">中文对照</summary>
              <p className="mt-2 whitespace-pre-wrap">{msg.message_zh}</p>
            </details>
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                onClick={async () => {
                  setCopied((await copyText(text)) ? "已复制" : "复制失败");
                  window.setTimeout(() => setCopied(""), 2500);
                }}
              >
                复制消息
              </Button>
              {copied && <span className="text-xs text-emerald-700">{copied}</span>}
              <span className="text-xs text-slate-400">软件不会代发：复制后自己发给达人。</span>
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          <Button
            variant="secondary"
            disabled={review.isPending || pending.length === 0}
            onClick={() => review.mutate("changes_requested")}
          >
            要求返修（{pending.length} 条反馈）
          </Button>
          <Button
            disabled={review.isPending}
            onClick={() =>
              window.confirm(`验收第 ${r.round_no} 轮 ${v.label}？验收后本轮计 1 条新视频，不能再上传或修改。`) &&
              review.mutate("accepted")
            }
          >
            验收通过
          </Button>
          {pending.length === 0 && <span className="text-xs text-slate-500">要求返修前至少添加 1 条时间码反馈。</span>}
        </div>
        {review.error && <ErrorText>{errorMessage(review.error)}</ErrorText>}
      </div>
    </Card>
  );
}

/** 某一轮的视频审核页：播放器 + 时间码反馈 + 审核结论。 */
export function RoundPage() {
  const { roundId = "" } = useParams();
  const qc = useQueryClient();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pos, setPos] = useState(0);
  const q = useQuery({
    queryKey: ["round", roundId],
    queryFn: () => unwrap(api.GET("/api/v1/rounds/{round_id}", { params: { path: { round_id: roundId } } })),
  });
  const r = q.data;
  const collab = useQuery({
    queryKey: ["collaboration", r?.collaboration_id],
    enabled: !!r,
    queryFn: () =>
      unwrap(api.GET("/api/v1/collaborations/{collab_id}", { params: { path: { collab_id: r!.collaboration_id } } })),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["round", roundId] });
    if (r) {
      qc.invalidateQueries({ queryKey: ["production", r.collaboration_id] });
      qc.invalidateQueries({ queryKey: ["collaboration", r.collaboration_id] });
    }
    qc.invalidateQueries({ queryKey: ["collaborations"] });
  };
  const del = useMutation({
    mutationFn: (fid: string) =>
      unwrap(api.DELETE("/api/v1/feedback-items/{fid}", { params: { path: { fid } } })),
    onSuccess: refresh,
  });

  if (q.error) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  if (!r) return <p className="text-sm text-slate-500">加载中…</p>;
  const v = r.videos.find((x) => x.id === selected) ?? r.videos[0];
  const prev = v ? r.videos.find((x) => x.version_no === v.version_no - 1) : undefined;
  const c = collab.data;
  const who = c ? (c.creator.nickname ?? c.creator.unique_id ?? "达人") : "";
  const seek = (ms: number) => {
    const el = videoRef.current;
    if (el) {
      el.currentTime = ms / 1000;
      el.pause();
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  };
  const reviewing = v && v.status === "in_review" && r.status === "in_review";

  return (
    <div className="space-y-4">
      <PageHeader
        title={`第 ${r.round_no} 轮视频${who ? ` · ${who}` : ""}`}
        subtitle={c ? `${c.campaign_name} · ${c.market_code} · ${c.product_name}` : undefined}
        actions={
          <Link to={`/collaborations/${r.collaboration_id}`} className="text-sm text-sky-700 hover:underline">
            ← 返回合作
          </Link>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={roundTone[r.status]}>{roundStatusLabels[r.status]}</Badge>
        {r.counted && <Badge tone="green">本轮已验收，计 1 条</Badge>}
        <span className="mx-2 h-4 w-px bg-slate-200" />
        {r.videos
          .slice()
          .reverse()
          .map((x) => (
            <Button key={x.id} variant={x.id === v?.id ? "primary" : "secondary"} onClick={() => setSelected(x.id)}>
              {x.label} · {videoStatusLabels[x.status]}
            </Button>
          ))}
        {r.can_upload && (
          <span className="ml-auto">
            <UploadButton
              round={r}
              onDone={(nv) => {
                setSelected(nv.id);
                refresh();
              }}
            />
          </span>
        )}
      </div>

      {!v ? (
        <Empty>这一轮还没有视频。</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
          <div className="space-y-3 lg:col-span-3">
            <Player v={v} videoRef={videoRef} feedback={v.feedback} onTime={(ms) => setPos(ms)} />
            {r.brief_version && (
              <details className="rounded-md border border-slate-200 p-3 text-sm">
                <summary className="cursor-pointer text-slate-600">
                  本轮拍摄包 v{r.brief_version.version_no}（{langName(r.brief_version.content_language)}，已锁定）
                </summary>
                <h4 className="mt-2 font-medium">{r.brief_version.title}</h4>
                <pre className="mt-1 whitespace-pre-wrap font-sans">{r.brief_version.body_zh || r.brief_version.body}</pre>
              </details>
            )}
          </div>
          <div className="space-y-4 lg:col-span-2">
            <Card title={`${v.label} 的反馈（${v.feedback.length}）`}>
              <div className="space-y-3">
                {reviewing && <AddFeedback v={v} posMs={pos} onAdded={refresh} />}
                <FeedbackList
                  items={v.feedback}
                  onSeek={seek}
                  onDelete={reviewing ? (id) => del.mutate(id) : undefined}
                />
                {v.review && (
                  <div className="rounded-md bg-slate-50 p-2 text-sm">
                    <span className="font-medium">{v.review.decision === "accepted" ? "验收通过" : "要求返修"}</span>
                    <span className="ml-2 text-xs text-slate-500">{fmtTime(v.review.created_at)}</span>
                    {v.review.summary && <p className="mt-1">{v.review.summary}</p>}
                    {v.review.message && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-slate-500">发给达人的消息</summary>
                        <p className="mt-1 whitespace-pre-wrap">{v.review.message}</p>
                      </details>
                    )}
                  </div>
                )}
              </div>
            </Card>
            {prev && prev.feedback.length > 0 && (
              <Card title={`上一版 ${prev.label} 的反馈：核对是否都改了`}>
                <FeedbackList items={prev.feedback} />
              </Card>
            )}
          </div>
        </div>
      )}
      {v && reviewing && (
        <ReviewPanel
          key={v.id}
          v={v}
          r={r}
          defaultLang={r.brief_version?.content_language ?? "en"}
          onDone={refresh}
        />
      )}
      {del.error && <ErrorText>{errorMessage(del.error)}</ErrorText>}
    </div>
  );
}
