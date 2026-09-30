import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Badge, Button, Card, ErrorText, Field, Input, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { CollaborationDetail, OutreachDraftIn, OutreachDraftView } from "@/lib/api/types";
import { copyText } from "@/lib/clipboard";
import { fmtTime } from "@/lib/labels";

type Purpose = NonNullable<OutreachDraftIn["purpose"]>;
type Channel = NonNullable<OutreachDraftIn["channel"]>;

const purposeLabels: Record<Purpose, string> = { invite: "第一次邀约", follow_up: "跟进" };
const channelLabels: Record<Channel, string> = { tiktok_message: "TikTok 私信", email: "邮件" };
const LANGS: [string, string][] = [
  ["", "站点默认语言"],
  ["en", "英语"],
  ["de", "德语"],
  ["fr", "法语"],
  ["it", "意大利语"],
  ["es", "西班牙语"],
  ["nl", "荷兰语"],
  ["pl", "波兰语"],
];
const langName = (code: string) => LANGS.find(([c]) => c === code)?.[1] ?? code;

function DraftView({ d, collabId, active }: { d: OutreachDraftView; collabId: string; active: boolean }) {
  const qc = useQueryClient();
  const [subject, setSubject] = useState(d.subject ?? "");
  const [message, setMessage] = useState(d.message);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    setSubject(d.subject ?? "");
    setMessage(d.message);
  }, [d.id, d.subject, d.message]);
  const sent = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/outreach-drafts/{draft_id}/mark-sent", { params: { path: { draft_id: d.id } }, body: { note: "" } })),
    onSuccess: (detail) => {
      qc.setQueryData(["collaboration", collabId], detail);
      qc.invalidateQueries({ queryKey: ["outreach", collabId] });
      qc.invalidateQueries({ queryKey: ["collaborations"] });
    },
  });
  const copy = async (what: string, text: string) => {
    setCopied((await copyText(text)) ? what : "复制失败，请手动选中复制");
    window.setTimeout(() => setCopied(""), 2500);
  };
  const edited = message !== d.message || subject !== (d.subject ?? "");
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <Badge tone="blue">{purposeLabels[d.purpose]}</Badge>
        <span>
          {channelLabels[d.channel]} · {langName(d.language)} · 生成于 {fmtTime(d.created_at)}
        </span>
        {d.sent_at && <Badge tone="green">已于 {fmtTime(d.sent_at)} 发送</Badge>}
      </div>
      {d.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 rounded-md bg-amber-50 py-2 pl-7 pr-3 text-sm text-amber-900">
          {d.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
      {d.channel === "email" && (
        <Field label="邮件标题">
          <div className="flex gap-2">
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} readOnly={!active} />
            <Button variant="secondary" onClick={() => copy("标题", subject)}>
              复制标题
            </Button>
          </div>
        </Field>
      )}
      <Field label="消息正文（可以直接修改后复制）" hint={`${message.length} 字符`}>
        <Textarea rows={8} value={message} onChange={(e) => setMessage(e.target.value)} readOnly={!active} />
      </Field>
      <details className="rounded-md bg-slate-50 p-3 text-sm">
        <summary className="cursor-pointer text-slate-600">中文对照{edited ? "（对应修改前的原文）" : ""}</summary>
        <p className="mt-2 whitespace-pre-wrap text-slate-700">{d.message_zh}</p>
        {d.personalization.length > 0 && (
          <div className="mt-2 text-xs text-slate-500">个性化依据：{d.personalization.join("；")}</div>
        )}
      </details>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => copy("正文", message)}>复制正文</Button>
        {!d.sent_at && (
          <Button variant="secondary" disabled={sent.isPending} onClick={() => sent.mutate()}>
            我已发送
          </Button>
        )}
        {copied && <span className="text-xs text-emerald-700">{copied.startsWith("复制失败") ? copied : `已复制${copied}`}</span>}
        <span className="text-xs text-slate-400">软件不会替你发送：复制后到 TikTok 或邮箱里发出，再点“我已发送”。</span>
      </div>
      {sent.error && <ErrorText>{errorMessage(sent.error)}</ErrorText>}
    </div>
  );
}

/** 邀约 / 跟进话术：AI 生成目标语言草稿 + 中文对照，本人复制发送。 */
export function OutreachCard({ c, followUpRequest }: { c: CollaborationDetail; followUpRequest: number }) {
  const qc = useQueryClient();
  const ref = useRef<HTMLDivElement>(null);
  const [purpose, setPurpose] = useState<Purpose>(c.status === "planned" ? "invite" : "follow_up");
  const [channel, setChannel] = useState<Channel>("tiktok_message");
  const [language, setLanguage] = useState("");
  const [tone, setTone] = useState<"friendly" | "professional">("friendly");
  const [extra, setExtra] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (followUpRequest > 0) {
      setPurpose("follow_up");
      ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [followUpRequest]);

  const drafts = useQuery({
    queryKey: ["outreach", c.id],
    queryFn: () =>
      unwrap(api.GET("/api/v1/collaborations/{collab_id}/outreach-drafts", { params: { path: { collab_id: c.id } } })),
  });
  const gen = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/outreach-drafts", {
          params: { path: { collab_id: c.id } },
          body: { purpose, channel, language: language || null, tone, extra },
        }),
      ),
    onSuccess: (d) => {
      setSelected(d.id);
      qc.invalidateQueries({ queryKey: ["outreach", c.id] });
    },
  });

  if (c.status === "closed" || c.status === "completed") return null;
  const list = drafts.data ?? [];
  const current = list.find((d) => d.id === selected) ?? list[0];

  return (
    <div ref={ref}>
      <Card title="邀约话术">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="用途">
              <Select value={purpose} onChange={(e) => setPurpose(e.target.value as Purpose)}>
                {Object.entries(purposeLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="发送渠道">
              <Select value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
                {Object.entries(channelLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="语言">
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                {LANGS.map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="语气">
              <Select value={tone} onChange={(e) => setTone(e.target.value as "friendly" | "professional")}>
                <option value="friendly">亲切自然</option>
                <option value="professional">正式专业</option>
              </Select>
            </Field>
          </div>
          <Field label="想补充的要点（可选）" hint="例如：可以先寄样再谈佣金；本周内回复优先安排">
            <Input value={extra} onChange={(e) => setExtra(e.target.value)} />
          </Field>
          <div className="flex items-center gap-3">
            <Button disabled={gen.isPending} onClick={() => gen.mutate()}>
              {gen.isPending ? "AI 生成中…（约 20–60 秒）" : "生成话术"}
            </Button>
            <span className="text-xs text-slate-500">根据产品资料、站点条款和达人公开数据生成；不会发送收件信息等个人数据。</span>
          </div>
          {gen.error && <ErrorText>{errorMessage(gen.error)}</ErrorText>}

          {current && (
            <div className="border-t border-slate-100 pt-4">
              <DraftView d={current} collabId={c.id} active />
            </div>
          )}
          {list.length > 1 && (
            <div className="border-t border-slate-100 pt-3">
              <p className="mb-1 text-xs text-slate-500">生成过的话术</p>
              <ul className="space-y-1 text-sm">
                {list.map((d) => (
                  <li key={d.id}>
                    <button
                      type="button"
                      className={`text-left hover:underline ${d.id === current?.id ? "font-medium text-slate-900" : "text-sky-700"}`}
                      onClick={() => setSelected(d.id)}
                    >
                      {fmtTime(d.created_at)} · {purposeLabels[d.purpose]} · {channelLabels[d.channel]} · {langName(d.language)}
                      {d.sent_at ? " · 已发送" : ""}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
