import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button, ErrorText, Field, Input, Select, Textarea } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey, unwrap } from "@/lib/api/client";
import type { RecipientIn, ShipmentIn, ShipmentView } from "@/lib/api/types";
import { shipmentKindLabels } from "@/lib/labels";

type Item = { sku: string; variant: string; quantity: string; unit_cost: string };
const emptyRecipient: RecipientIn = {
  name: "",
  phone: "",
  email: "",
  address_line1: "",
  address_line2: "",
  city: "",
  region: "",
  postcode: "",
  country_code: "",
};

/** 新建或修改寄样单。收件信息提交后只以密文保存；修改寄样单会使已有的确认作废。 */
export function ShipmentForm({
  collabId,
  currency,
  marketCountry,
  existing,
  onDone,
  onCancel,
}: {
  collabId: string;
  currency: string;
  marketCountry: string;
  existing?: ShipmentView;
  onDone: (s: ShipmentView) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<ShipmentIn["kind"]>(existing?.kind ?? "initial_sample");
  const [items, setItems] = useState<Item[]>(
    existing?.items.map((i) => ({
      sku: i.sku,
      variant: i.variant ?? "",
      quantity: String(i.quantity),
      unit_cost: i.unit_cost ?? "",
    })) ?? [{ sku: "", variant: "", quantity: "1", unit_cost: "" }],
  );
  const [cap, setCap] = useState(existing?.cost_cap_amount ?? "");
  const [cur, setCur] = useState(existing?.currency ?? currency);
  const [note, setNote] = useState(existing?.note ?? "");
  const [changeRecipient, setChangeRecipient] = useState(!existing);
  const [rec, setRec] = useState<RecipientIn>({ ...emptyRecipient, country_code: marketCountry });
  const keyRef = useRef(newIdempotencyKey());

  const body = (): ShipmentIn => ({
    kind,
    items: items.map((i) => ({
      sku: i.sku,
      variant: i.variant || null,
      quantity: Number(i.quantity),
      unit_cost: i.unit_cost || null,
    })),
    cost_cap_amount: cap || null,
    currency: cur,
    note,
    recipient: changeRecipient
      ? {
          ...rec,
          phone: rec.phone || null,
          email: rec.email || null,
          address_line2: rec.address_line2 || null,
          region: rec.region || null,
        }
      : null,
  });
  const save = useMutation({
    mutationFn: () =>
      existing
        ? unwrap(api.PUT("/api/v1/shipments/{shipment_id}", { params: { path: { shipment_id: existing.id } }, body: body() }))
        : unwrap(
            api.POST("/api/v1/collaborations/{collab_id}/shipments", {
              params: { path: { collab_id: collabId }, header: { "Idempotency-Key": keyRef.current } },
              body: body(),
            }),
          ),
    onSuccess: (s) => {
      keyRef.current = newIdempotencyKey();
      onDone(s);
    },
  });
  const setItem = (i: number, patch: Partial<Item>) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const r = (k: keyof RecipientIn) => ({
    value: (rec[k] as string | null | undefined) ?? "",
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setRec({ ...rec, [k]: e.target.value }),
    autoComplete: "off",
  });
  const valid =
    items.every((i) => i.sku.trim() && Number(i.quantity) >= 1) &&
    (!changeRecipient || (rec.name && rec.address_line1 && rec.city && rec.postcode && rec.country_code.length === 2));

  return (
    <div className="space-y-4 rounded-md border border-slate-200 bg-slate-50 p-4">
      {existing && existing.status !== "draft" && (
        <p className="rounded bg-amber-50 px-3 py-2 text-sm text-amber-900">修改后，之前的核对和确认都会作废，需要重新核对确认。</p>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Field label="类型">
          <Select value={kind} onChange={(e) => setKind(e.target.value as ShipmentIn["kind"])}>
            {Object.entries(shipmentKindLabels).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="费用上限（样品 + 运费）" hint="不知道可以留空，显示为“未知”">
          <Input inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} placeholder="20" />
        </Field>
        <Field label="币种">
          <Input value={cur} maxLength={3} onChange={(e) => setCur(e.target.value.toUpperCase())} />
        </Field>
      </div>
      <div>
        <div className="mb-1 text-sm font-medium">样品明细</div>
        <div className="space-y-2">
          {items.map((it, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Input className="w-32" placeholder="SKU" value={it.sku} onChange={(e) => setItem(i, { sku: e.target.value })} />
              <Input className="w-32" placeholder="规格（可选）" value={it.variant} onChange={(e) => setItem(i, { variant: e.target.value })} />
              <Input className="w-20" inputMode="numeric" placeholder="数量" value={it.quantity} onChange={(e) => setItem(i, { quantity: e.target.value })} />
              <Input className="w-32" inputMode="decimal" placeholder={`单件成本 ${cur}`} value={it.unit_cost} onChange={(e) => setItem(i, { unit_cost: e.target.value })} />
              {items.length > 1 && (
                <Button variant="ghost" onClick={() => setItems(items.filter((_, j) => j !== i))}>
                  删除
                </Button>
              )}
            </div>
          ))}
          {items.length < 10 && (
            <Button variant="ghost" onClick={() => setItems([...items, { sku: "", variant: "", quantity: "1", unit_cost: "" }])}>
              + 添加一行
            </Button>
          )}
        </div>
      </div>
      <div>
        <div className="mb-1 flex items-center gap-3 text-sm font-medium">
          收件信息
          <span className="text-xs font-normal text-slate-500">加密保存在本机；列表里只显示脱敏摘要</span>
          {existing && (
            <label className="flex items-center gap-1 text-xs font-normal">
              <input type="checkbox" checked={changeRecipient} onChange={(e) => setChangeRecipient(e.target.checked)} />
              修改收件信息（不勾选则沿用 {existing.recipient_masked}）
            </label>
          )}
        </div>
        {changeRecipient && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <Input placeholder="收件人姓名" {...r("name")} />
            <Input placeholder="电话（可选）" {...r("phone")} />
            <Input placeholder="邮箱（可选）" {...r("email")} />
            <Input className="md:col-span-2" placeholder="地址第 1 行" {...r("address_line1")} />
            <Input placeholder="地址第 2 行（可选）" {...r("address_line2")} />
            <Input placeholder="城市" {...r("city")} />
            <Input placeholder="州 / 郡（可选）" {...r("region")} />
            <div className="flex gap-2">
              <Input placeholder="邮编" {...r("postcode")} />
              <Input className="w-20" placeholder="国家" maxLength={2} {...r("country_code")} />
            </div>
          </div>
        )}
      </div>
      <Field label="备注（可选）">
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {save.error && <ErrorText>{errorMessage(save.error)}</ErrorText>}
      <div className="flex gap-2">
        <Button disabled={!valid || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "保存中…" : existing ? "保存修改" : "保存寄样单（草稿）"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}
