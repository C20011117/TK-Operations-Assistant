import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { Badge, Button, Card, Empty, ErrorText, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { CollaborationDetail } from "@/lib/api/types";
import { agreedViaLabels, closedReasonLabels, collabStatusLabels, fmtTime, localNow } from "@/lib/labels";

import { collabTone } from "./CollaborationsPage";
import { ShipmentCard } from "./ShipmentCard";
import { ShipmentForm } from "./ShipmentForm";

type ClosedReason = keyof typeof closedReasonLabels;
type Via = keyof typeof agreedViaLabels;

/** 平台站点码 → 收件国家码（英国站 UK 对应国家码 GB）。 */
const countryOf = (market: string) => (market === "UK" ? "GB" : market);

function useSet(id: string) {
  const qc = useQueryClient();
  return (d: CollaborationDetail) => {
    qc.setQueryData(["collaboration", id], d);
    qc.invalidateQueries({ queryKey: ["collaborations"] });
    qc.invalidateQueries({ queryKey: ["decisions", d.campaign_market_id] });
  };
}

function ProgressCard({ c }: { c: CollaborationDetail }) {
  const set = useSet(c.id);
  const [note, setNote] = useState("");
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState<ClosedReason>("no_reply");
  const move = useMutation({
    mutationFn: (to: "contacting" | "negotiating" | "closed") =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/transitions", {
          params: { path: { collab_id: c.id } },
          body: { to, note, revision: c.revision, closed_reason: to === "closed" ? reason : null },
        }),
      ),
    onSuccess: (d) => {
      setNote("");
      setClosing(false);
      set(d);
    },
  });
  const label = { contacting: c.status === "closed" ? "重新联系" : "已联系", negotiating: "开始洽谈", closed: "关闭合作" } as const;
  const steps = c.allowed_transitions.filter((t) => t !== "closed") as ("contacting" | "negotiating")[];
  return (
    <Card title="合作进度">
      <div className="space-y-3">
        <p className="text-sm">
          下一步：<span className="font-medium">{c.next_step ?? "—"}</span>
          {c.closed_reason && <span className="ml-2 text-slate-500">关闭原因：{closedReasonLabels[c.closed_reason as ClosedReason]}</span>}
        </p>
        {(steps.length > 0 || c.allowed_transitions.includes("closed")) && (
          <>
            <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注（可选），例如：已通过 TikTok 私信发出邀约" />
            <div className="flex flex-wrap items-center gap-2">
              {steps.map((t) => (
                <Button key={t} variant="secondary" disabled={move.isPending} onClick={() => move.mutate(t)}>
                  {label[t]}
                </Button>
              ))}
              {c.allowed_transitions.includes("closed") &&
                (closing ? (
                  <>
                    <Select className="w-40" value={reason} onChange={(e) => setReason(e.target.value as ClosedReason)}>
                      {Object.entries(closedReasonLabels).map(([k, v]) => (
                        <option key={k} value={k}>
                          {v}
                        </option>
                      ))}
                    </Select>
                    <Button disabled={move.isPending} onClick={() => move.mutate("closed")}>
                      确认关闭
                    </Button>
                    <Button variant="ghost" onClick={() => setClosing(false)}>
                      取消
                    </Button>
                  </>
                ) : (
                  <Button variant="ghost" onClick={() => setClosing(true)}>
                    关闭合作
                  </Button>
                ))}
            </div>
            {closing && <p className="text-xs text-slate-500">关闭后，还没寄出的寄样单会一并取消，已有的确认作废。</p>}
          </>
        )}
        {move.error && <ErrorText>{errorMessage(move.error)}</ErrorText>}
      </div>
    </Card>
  );
}

function AgreementCard({ c }: { c: CollaborationDetail }) {
  const set = useSet(c.id);
  const [f, setF] = useState({
    agreed_via: "tiktok_message" as Via,
    agreed_on: new Date().toISOString().slice(0, 10),
    agreed_video_count: "1",
    sample_included: true,
    terms_note: "",
  });
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/confirm-agreement", {
          params: { path: { collab_id: c.id } },
          body: { ...f, agreed_video_count: Number(f.agreed_video_count), revision: c.revision },
        }),
      ),
    onSuccess: set,
  });
  const a = c.agreement;
  return (
    <Card title="双方约定">
      {a ? (
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="text-slate-500">达成方式</dt>
          <dd>
            {agreedViaLabels[a.agreed_via as Via] ?? a.agreed_via} · {a.agreed_on}
          </dd>
          <dt className="text-slate-500">新视频数量</dt>
          <dd>{a.agreed_video_count} 条</dd>
          <dt className="text-slate-500">寄样</dt>
          <dd>{a.sample_included ? "包含寄样" : "不寄样"}</dd>
          <dt className="text-slate-500">约定内容</dt>
          <dd className="whitespace-pre-wrap">{a.terms_note}</dd>
          <dt className="text-slate-500">记录时间</dt>
          <dd>{fmtTime(a.recorded_at)}</dd>
        </dl>
      ) : c.can_confirm_agreement ? (
        <div className="space-y-3">
          <p className="text-sm text-slate-600">双方谈妥后在这里记录。匹配结果不能代替约定；记录后才能安排寄样。</p>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <Field label="怎么谈成的">
              <Select value={f.agreed_via} onChange={(e) => setF({ ...f, agreed_via: e.target.value as Via })}>
                {Object.entries(agreedViaLabels).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="达成日期">
              <Input type="date" value={f.agreed_on} onChange={(e) => setF({ ...f, agreed_on: e.target.value })} />
            </Field>
            <Field label="约定新视频数量">
              <Input inputMode="numeric" value={f.agreed_video_count} onChange={(e) => setF({ ...f, agreed_video_count: e.target.value })} />
            </Field>
          </div>
          <label className="flex items-center gap-1.5 text-sm">
            <input type="checkbox" checked={f.sample_included} onChange={(e) => setF({ ...f, sample_included: e.target.checked })} />
            约定包含寄样
          </label>
          <Field label="约定内容" hint="报价、佣金、交付时间等；按双方实际说好的写">
            <Textarea rows={3} value={f.terms_note} onChange={(e) => setF({ ...f, terms_note: e.target.value })} />
          </Field>
          <Button disabled={!f.terms_note.trim() || Number(f.agreed_video_count) < 1 || m.isPending} onClick={() => m.mutate()}>
            记录双方约定
          </Button>
          {m.error && <ErrorText>{errorMessage(m.error)}</ErrorText>}
        </div>
      ) : (
        <Empty>没有约定记录。</Empty>
      )}
    </Card>
  );
}

function ShipmentsCard({ c, refresh }: { c: CollaborationDetail; refresh: () => void }) {
  const [creating, setCreating] = useState(false);
  const country = countryOf(c.market_code);
  return (
    <Card
      title="寄样"
      actions={
        c.can_create_shipment &&
        !creating && (
          <Button variant="secondary" onClick={() => setCreating(true)}>
            新建寄样单
          </Button>
        )
      }
    >
      <div className="space-y-3">
        {!c.can_create_shipment && c.shipments.length === 0 && (
          <p className="text-sm text-slate-500">记录双方约定后才能安排寄样。</p>
        )}
        {creating && (
          <ShipmentForm
            collabId={c.id}
            currency={c.reporting_currency}
            marketCountry={country}
            onDone={() => {
              setCreating(false);
              refresh();
            }}
            onCancel={() => setCreating(false)}
          />
        )}
        {c.shipments.map((s) => (
          <ShipmentCard key={s.id} s={s} currency={c.reporting_currency} marketCountry={country} onChanged={refresh} />
        ))}
      </div>
    </Card>
  );
}

export function CollaborationDetailPage() {
  const { collabId = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["collaboration", collabId],
    queryFn: () => unwrap(api.GET("/api/v1/collaborations/{collab_id}", { params: { path: { collab_id: collabId } } })),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["collaboration", collabId] });
    qc.invalidateQueries({ queryKey: ["collaborations"] });
  };
  if (q.error) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  if (!q.data) return <p className="text-sm text-slate-500">加载中…</p>;
  const c = q.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            {c.creator.nickname ?? c.creator.unique_id}
            {c.creator.unique_id && <span className="text-base font-normal text-slate-500">@{c.creator.unique_id}</span>}
            <Badge tone={collabTone[c.status]}>{collabStatusLabels[c.status]}</Badge>
          </span>
        }
        subtitle={
          <span>
            <Link className="text-sky-700 hover:underline" to={`/campaigns/${c.campaign_id}`}>
              {c.campaign_name}
            </Link>{" "}
            · {c.market_code}（{c.reporting_currency}，当地 {localNow(c.time_zone)}）· {c.product_name} v{c.product_version_no}
            {c.creator.profile_url && <span className="ml-2 select-all text-xs text-slate-500">{c.creator.profile_url}</span>}
          </span>
        }
        actions={
          <Link className="text-sm text-sky-700 hover:underline" to="/collaborations">
            ← 我的合作
          </Link>
        }
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ProgressCard c={c} />
        <AgreementCard c={c} />
      </div>
      <ShipmentsCard c={c} refresh={refresh} />
      <Card title="历史">
        <ul className="space-y-1 text-sm">
          {c.events.map((e) => (
            <li key={e.id} className="flex gap-3">
              <span className="w-40 shrink-0 text-xs text-slate-500">{fmtTime(e.created_at)}</span>
              <span>
                {e.to_status && e.kind !== "shipment" && (
                  <Badge tone="slate">{collabStatusLabels[e.to_status as keyof typeof collabStatusLabels] ?? e.to_status}</Badge>
                )}{" "}
                {e.note}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
