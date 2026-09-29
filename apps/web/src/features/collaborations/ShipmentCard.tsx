import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Badge, Button, ErrorText, Field, Input, Select } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey, unwrap } from "@/lib/api/client";
import type { ConfirmationPreview, ShipmentView } from "@/lib/api/types";
import { deliveryLabels, fmtTime, money, shipmentKindLabels, shipmentStatusLabels } from "@/lib/labels";

import { ShipmentForm } from "./ShipmentForm";

const statusTone = { draft: "slate", awaiting_confirmation: "amber", confirmed: "blue", dispatched: "green", cancelled: "slate" } as const;
const deliveryTone = { unknown: "amber", in_transit: "blue", delivered: "green", exception: "red", returned: "red" } as const;
const today = () => new Date().toISOString().slice(0, 10);

function ConfirmPanel({ s, onDone, onClose }: { s: ShipmentView; onDone: () => void; onClose: () => void }) {
  const [preview, setPreview] = useState<ConfirmationPreview | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  const load = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/shipments/{shipment_id}/confirmation-preview", { params: { path: { shipment_id: s.id } } })),
    onSuccess: (p) => {
      keyRef.current = newIdempotencyKey();
      setPreview(p);
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/shipments/{shipment_id}/confirm", {
          params: { path: { shipment_id: s.id }, header: { "Idempotency-Key": keyRef.current } },
          body: { payload_version: preview!.payload_version, payload_hash: preview!.payload_hash },
        }),
      ),
    onSuccess: onDone,
  });
  if (!preview) {
    return (
      <div className="rounded-md bg-slate-50 p-3 text-sm">
        <p className="mb-2">先冻结当前寄样内容生成核对摘要；核对无误后再由你本人确认。确认后修改任何内容都需要重新确认。</p>
        <div className="flex gap-2">
          <Button onClick={() => load.mutate()} disabled={load.isPending}>
            {load.isPending ? "生成中…" : "生成核对摘要"}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
        </div>
        {load.error && <ErrorText>{errorMessage(load.error)}</ErrorText>}
      </div>
    );
  }
  const sum = preview.summary as {
    creator: string;
    campaign: string;
    market_code: string;
    product: string;
    kind: keyof typeof shipmentKindLabels;
    items: { sku: string; variant: string | null; quantity: number; unit_cost: string | null }[];
    items_cost_total: string | null;
    cost_cap_amount: string | null;
    currency: string;
    recipient: string;
  };
  return (
    <div className="space-y-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm">
      <div className="font-medium">请核对寄样内容（第 {preview.payload_version} 版）</div>
      <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1">
        <dt className="text-slate-500">达人</dt>
        <dd>@{sum.creator}</dd>
        <dt className="text-slate-500">任务</dt>
        <dd>
          {sum.campaign} · {sum.market_code} · {sum.product}
        </dd>
        <dt className="text-slate-500">类型</dt>
        <dd>{shipmentKindLabels[sum.kind]}</dd>
        <dt className="text-slate-500">明细</dt>
        <dd>
          {sum.items.map((i, k) => (
            <div key={k}>
              {i.sku}
              {i.variant && `（${i.variant}）`} × {i.quantity}，单件 {money(i.unit_cost, sum.currency)}
            </div>
          ))}
        </dd>
        <dt className="text-slate-500">样品成本合计</dt>
        <dd>{money(sum.items_cost_total, sum.currency)}</dd>
        <dt className="text-slate-500">费用上限</dt>
        <dd>{money(sum.cost_cap_amount, sum.currency)}</dd>
        <dt className="text-slate-500">收件</dt>
        <dd>{sum.recipient}（完整信息可在寄样卡上查看）</dd>
      </dl>
      <p className="text-xs text-slate-600">确认有效期 {preview.expires_in_days} 天。确认只表示你同意寄这批样品，系统不会替你下物流单。</p>
      <div className="flex gap-2">
        <Button onClick={() => confirm.mutate()} disabled={confirm.isPending}>
          {confirm.isPending ? "确认中…" : "我已核对，确认寄样"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          返回
        </Button>
      </div>
      {confirm.error && <ErrorText>{errorMessage(confirm.error)}</ErrorText>}
    </div>
  );
}

function DispatchForm({ s, onDone, onClose }: { s: ShipmentView; onDone: () => void; onClose: () => void }) {
  const [f, setF] = useState({ dispatched_at: today(), carrier: "", tracking_number: "", note: "" });
  const keyRef = useRef(newIdempotencyKey());
  const m = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/shipments/{shipment_id}/register-dispatch", {
          params: { path: { shipment_id: s.id }, header: { "Idempotency-Key": keyRef.current } },
          body: { ...f, carrier: f.carrier || null, tracking_number: f.tracking_number || null },
        }),
      ),
    onSuccess: onDone,
  });
  return (
    <div className="space-y-3 rounded-md bg-slate-50 p-3">
      <p className="text-sm text-slate-600">你已经把样品寄出后在这里登记。系统只记录这个事实，不会调用物流接口；签收状态在你登记物流节点前一直是“未知”。</p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <Field label="寄出日期">
          <Input type="date" max={today()} value={f.dispatched_at} onChange={(e) => setF({ ...f, dispatched_at: e.target.value })} />
        </Field>
        <Field label="快递公司（可选）">
          <Input value={f.carrier} onChange={(e) => setF({ ...f, carrier: e.target.value })} placeholder="Royal Mail" />
        </Field>
        <Field label="快递单号（可选，加密保存）">
          <Input autoComplete="off" value={f.tracking_number} onChange={(e) => setF({ ...f, tracking_number: e.target.value })} />
        </Field>
      </div>
      <div className="flex gap-2">
        <Button onClick={() => m.mutate()} disabled={!f.dispatched_at || m.isPending}>
          {m.isPending ? "登记中…" : "登记已寄出"}
        </Button>
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
      </div>
      {m.error && <ErrorText>{errorMessage(m.error)}</ErrorText>}
    </div>
  );
}

function EventForm({ s, onDone, onClose }: { s: ShipmentView; onDone: () => void; onClose: () => void }) {
  const [f, setF] = useState<{ status: "in_transit" | "delivered" | "exception" | "returned"; occurred_at: string; note: string }>({
    status: "delivered",
    occurred_at: today(),
    note: "",
  });
  const m = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/shipments/{shipment_id}/events", { params: { path: { shipment_id: s.id } }, body: f })),
    onSuccess: onDone,
  });
  return (
    <div className="space-y-3 rounded-md bg-slate-50 p-3">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <Field label="物流节点">
          <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as typeof f.status })}>
            {(["in_transit", "delivered", "exception", "returned"] as const).map((k) => (
              <option key={k} value={k}>
                {deliveryLabels[k]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="发生日期" hint="按发生日期决定当前状态，晚登记不会覆盖更晚发生的节点">
          <Input type="date" max={today()} value={f.occurred_at} onChange={(e) => setF({ ...f, occurred_at: e.target.value })} />
        </Field>
        <Field label="说明（可选）">
          <Input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="例如：达人私信说已收到" />
        </Field>
      </div>
      <div className="flex gap-2">
        <Button onClick={() => m.mutate()} disabled={m.isPending}>
          登记
        </Button>
        <Button variant="ghost" onClick={onClose}>
          取消
        </Button>
      </div>
      {m.error && <ErrorText>{errorMessage(m.error)}</ErrorText>}
    </div>
  );
}

function RecipientReveal({ s }: { s: ShipmentView }) {
  const m = useMutation({
    mutationFn: () => unwrap(api.GET("/api/v1/shipments/{shipment_id}/recipient", { params: { path: { shipment_id: s.id } } })),
  });
  if (!m.data) {
    return (
      <>
        <Button variant="ghost" onClick={() => m.mutate()} disabled={m.isPending}>
          查看完整收件信息
        </Button>
        {m.error && <ErrorText>{errorMessage(m.error)}</ErrorText>}
      </>
    );
  }
  const r = m.data.recipient;
  const lines = [r.name, r.phone, r.email, r.address_line1, r.address_line2, [r.city, r.region, r.postcode].filter(Boolean).join(", "), r.country_code].filter(Boolean);
  return (
    <div className="mt-1 rounded bg-white p-2 text-sm ring-1 ring-slate-200">
      <pre className="select-all whitespace-pre-wrap font-sans">{lines.join("\n")}</pre>
      {m.data.tracking_number && <div className="mt-1 text-xs text-slate-500">快递单号：<span className="select-all">{m.data.tracking_number}</span></div>}
      <Button variant="ghost" onClick={() => m.reset()}>
        隐藏
      </Button>
    </div>
  );
}

type Mode = null | "edit" | "confirm" | "dispatch" | "event";

export function ShipmentCard({
  s,
  currency,
  marketCountry,
  onChanged,
}: {
  s: ShipmentView;
  currency: string;
  marketCountry: string;
  onChanged: () => void;
}) {
  const [mode, setMode] = useState<Mode>(null);
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/shipments/{shipment_id}/cancel", { params: { path: { shipment_id: s.id } } })),
    onSuccess: onChanged,
  });
  const done = () => {
    setMode(null);
    onChanged();
  };
  const editable = ["draft", "awaiting_confirmation", "confirmed"].includes(s.status);

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{shipmentKindLabels[s.kind]}</span>
        <Badge tone={statusTone[s.status]}>{shipmentStatusLabels[s.status]}</Badge>
        {s.status === "dispatched" && <Badge tone={deliveryTone[s.delivery_status]}>{deliveryLabels[s.delivery_status]}</Badge>}
        <span className="text-xs text-slate-400">第 {s.payload_version} 版</span>
        <span className="ml-auto text-xs text-slate-500">创建于 {fmtTime(s.created_at)}</span>
      </div>
      <div className="grid grid-cols-1 gap-2 text-sm md:grid-cols-2">
        <div>
          {s.items.map((i, k) => (
            <div key={k}>
              {i.sku}
              {i.variant && `（${i.variant}）`} × {i.quantity} · 单件 {money(i.unit_cost, s.currency)}
            </div>
          ))}
          <div className="text-xs text-slate-500">
            样品成本合计 {money(s.items_cost_total, s.currency)} · 费用上限 {money(s.cost_cap_amount, s.currency)}
          </div>
        </div>
        <div>
          <div>收件：{s.recipient_masked}</div>
          {s.carrier || s.tracking_masked ? (
            <div className="text-xs text-slate-500">
              {s.carrier} {s.tracking_masked}
            </div>
          ) : null}
          {s.dispatched_at && <div className="text-xs text-slate-500">寄出：{fmtTime(s.dispatched_at)}</div>}
          {s.delivered_at && <div className="text-xs text-slate-500">签收：{fmtTime(s.delivered_at)}</div>}
          {s.confirmation && (
            <div className="text-xs text-slate-500">
              本人确认于 {fmtTime(s.confirmation.confirmed_at)}
              {s.confirmation.consumed_at ? "（已用于登记寄出）" : s.confirmation.valid ? `，${fmtTime(s.confirmation.expires_at)} 前有效` : "（已过期）"}
            </div>
          )}
          {s.status !== "cancelled" && <RecipientReveal s={s} />}
        </div>
      </div>
      {s.status === "dispatched" && s.delivery_status === "unknown" && (
        <p className="text-xs text-amber-700">签收未知：还没有登记物流节点。系统不会因为时间过去就当作已签收。</p>
      )}
      {s.events.length > 0 && (
        <ul className="space-y-0.5 text-xs text-slate-600">
          {s.events.map((e) => (
            <li key={e.id}>
              {e.occurred_at.slice(0, 10)} · {deliveryLabels[e.status as keyof typeof deliveryLabels]}
              {e.note && ` · ${e.note}`}
              <span className="text-slate-400">（登记于 {fmtTime(e.observed_at)}）</span>
            </li>
          ))}
        </ul>
      )}

      {mode === "edit" && (
        <ShipmentForm collabId={s.collaboration_id} currency={currency} marketCountry={marketCountry} existing={s} onDone={done} onCancel={() => setMode(null)} />
      )}
      {mode === "confirm" && <ConfirmPanel s={s} onDone={done} onClose={() => setMode(null)} />}
      {mode === "dispatch" && <DispatchForm s={s} onDone={done} onClose={() => setMode(null)} />}
      {mode === "event" && <EventForm s={s} onDone={done} onClose={() => setMode(null)} />}

      {mode === null && (
        <div className="flex flex-wrap gap-2">
          {(s.next_action === "preview" || s.next_action === "confirm") && <Button onClick={() => setMode("confirm")}>核对并确认寄样</Button>}
          {s.next_action === "register_dispatch" && <Button onClick={() => setMode("dispatch")}>登记已寄出</Button>}
          {s.next_action === "record_delivery" && <Button onClick={() => setMode("event")}>登记物流节点</Button>}
          {s.status === "dispatched" && s.next_action !== "record_delivery" && (
            <Button variant="ghost" onClick={() => setMode("event")}>
              补登物流节点
            </Button>
          )}
          {editable && (
            <Button variant="secondary" onClick={() => setMode("edit")}>
              修改
            </Button>
          )}
          {editable && (
            <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
              取消寄样单
            </Button>
          )}
        </div>
      )}
      {cancel.error && <ErrorText>{errorMessage(cancel.error)}</ErrorText>}
    </div>
  );
}
