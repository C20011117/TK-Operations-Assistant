import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button, Card, ErrorText, Field, Input, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { MarketTermIn, ProductCategory, ProductDetail, ProductVersion } from "@/lib/api/types";
import { fromLines, samplePolicyLabels, toLines } from "@/lib/labels";

import { CategoryPicker } from "./CategoryPicker";

type TermRow = MarketTermIn & { enabled: boolean };

function initialTerms(markets: { market_code: string }[], draft: ProductVersion): TermRow[] {
  const byCode = new Map(draft.market_terms.map((t) => [t.market_code, t]));
  return markets.map((m) => {
    const t = byCode.get(m.market_code);
    return t
      ? {
          enabled: true,
          market_code: t.market_code,
          price_status: t.price_status,
          price_amount: t.price_amount ?? null,
          sample_policy: t.sample_policy ?? "unknown",
          commission_min_pct: t.commission_min_pct ?? null,
          commission_max_pct: t.commission_max_pct ?? null,
          quote_valid_until: t.quote_valid_until ?? null,
          notes: t.notes ?? "",
        }
      : { enabled: false, market_code: m.market_code, price_status: "unknown", sample_policy: "unknown", notes: "" };
  });
}

/** 草稿编辑：产品事实 + 各站点价格条款。保存草稿不影响当前版本和已建任务。 */
export function DraftEditor({ product, draft }: { product: ProductDetail; draft: ProductVersion }) {
  const qc = useQueryClient();
  const markets = useQuery({
    queryKey: ["markets", "europe"],
    queryFn: () => unwrap(api.GET("/api/v1/markets", { params: { query: { region_group: "europe" } } })),
    staleTime: Infinity,
  });
  const f = draft.facts;
  const [facts, setFacts] = useState({
    summary: f.summary ?? "",
    selling_points: toLines(f.selling_points ?? []),
    use_scenarios: toLines(f.use_scenarios ?? []),
    target_customers: f.target_customers ?? "",
    forbidden_claims: toLines(f.forbidden_claims ?? []),
    reference_links: toLines(f.reference_links ?? []),
    notes: f.notes ?? "",
  });
  const [category, setCategory] = useState<ProductCategory | null>(f.category ?? null);
  const [terms, setTerms] = useState<TermRow[] | null>(null);
  const rows = terms ?? (markets.data ? initialTerms(markets.data, draft) : null);
  const currency = new Map(markets.data?.map((m) => [m.market_code, m.settlement_currency]));

  const setRow = (code: string, patch: Partial<TermRow>) =>
    setTerms((rows ?? []).map((r) => (r.market_code === code ? { ...r, ...patch } : r)));

  const body = () => ({
    facts: {
      summary: facts.summary,
      selling_points: fromLines(facts.selling_points),
      use_scenarios: fromLines(facts.use_scenarios),
      target_customers: facts.target_customers,
      forbidden_claims: fromLines(facts.forbidden_claims),
      reference_links: fromLines(facts.reference_links),
      notes: facts.notes,
      category,
    },
    market_terms: (rows ?? [])
      .filter((r) => r.enabled)
      .map(({ enabled: _enabled, ...t }) => ({
        ...t,
        price_amount: t.price_status === "known" ? t.price_amount || null : null,
        commission_min_pct: t.commission_min_pct || null,
        commission_max_pct: t.commission_max_pct || null,
        quote_valid_until: t.quote_valid_until || null,
      })),
  });

  const refresh = (d: ProductDetail) => qc.setQueryData(["product", product.id], d);
  const save = useMutation({
    mutationFn: () => unwrap(api.PUT("/api/v1/products/{product_id}/draft", { params: { path: { product_id: product.id } }, body: body() })),
    onSuccess: refresh,
  });
  const confirm = useMutation({
    mutationFn: async () => {
      await unwrap(api.PUT("/api/v1/products/{product_id}/draft", { params: { path: { product_id: product.id } }, body: body() }));
      return unwrap(api.POST("/api/v1/products/{product_id}/draft/confirm", { params: { path: { product_id: product.id } } }));
    },
    onSuccess: (d) => {
      refresh(d);
      qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
  const discard = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/products/{product_id}/draft", { params: { path: { product_id: product.id } } })),
    onSuccess: refresh,
  });
  const busy = save.isPending || confirm.isPending || discard.isPending;
  const err = save.error ?? confirm.error ?? discard.error;
  const t = (k: keyof typeof facts) => ({
    value: facts[k],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setFacts({ ...facts, [k]: e.target.value }),
  });

  return (
    <Card
      title={`编辑草稿 v${draft.version_no}`}
      actions={
        <div className="flex gap-2">
          {product.current && (
            <Button variant="ghost" disabled={busy} onClick={() => discard.mutate()}>
              放弃草稿
            </Button>
          )}
          <Button variant="secondary" disabled={busy} onClick={() => save.mutate()}>
            {save.isPending ? "保存中…" : "保存草稿"}
          </Button>
          <Button disabled={busy} onClick={() => confirm.mutate()}>
            {confirm.isPending ? "确认中…" : `确认为 v${draft.version_no}`}
          </Button>
        </div>
      }
    >
      <p className="mb-4 text-sm text-slate-500">
        列表类内容每行填一项。确认后这一版本不能再修改；之后的修改会生成新版本，已建任务仍使用原版本。
      </p>
      {err && (
        <div className="mb-4">
          <ErrorText>{errorMessage(err)}</ErrorText>
        </div>
      )}
      {save.isSuccess && !busy && !err && <p className="mb-4 text-sm text-emerald-700">草稿已保存</p>}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <Field label="产品简介（必填）" hint="一句话说明是什么、解决什么问题">
            <Textarea rows={2} {...t("summary")} />
          </Field>
        </div>
        <div className="md:col-span-2">
          <Field label="TikTok 商品类目" hint="找人时按这个类目搜索“带过同类商品”的达人，例如摄像头 → 手机与数码 › 摄影摄像 › 监控摄像设备">
            <CategoryPicker value={category} onChange={setCategory} defaultQuery={product.name} />
          </Field>
        </div>
        <Field label="主要卖点（至少一项）">
          <Textarea rows={5} {...t("selling_points")} placeholder={"便携，随时随地使用\n30 秒出汁"} />
        </Field>
        <Field label="使用场景">
          <Textarea rows={5} {...t("use_scenarios")} placeholder={"办公室\n健身后"} />
        </Field>
        <Field label="禁用表述" hint="拍摄包和推荐理由中不得出现的宣称">
          <Textarea rows={4} {...t("forbidden_claims")} placeholder="不得宣称减肥功效" />
        </Field>
        <Field label="参考链接" hint="商品页、参考视频，需以 http(s):// 开头">
          <Textarea rows={4} {...t("reference_links")} />
        </Field>
        <Field label="目标客户">
          <Textarea rows={2} {...t("target_customers")} />
        </Field>
        <Field label="备注">
          <Textarea rows={2} {...t("notes")} />
        </Field>
      </div>

      <h3 className="mt-6 mb-2 text-sm font-medium">各站点价格条款</h3>
      <p className="mb-3 text-xs text-slate-500">
        勾选要销售的站点。币种由站点决定；不知道价格请选“未知”，不要填 0。创建某站点的找人任务前，该站点必须有价格条款。
      </p>
      {!rows ? (
        <p className="text-sm text-slate-500">加载站点…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                <th className="py-2 pr-2 font-medium">站点</th>
                <th className="py-2 pr-2 font-medium">价格</th>
                <th className="py-2 pr-2 font-medium">金额</th>
                <th className="py-2 pr-2 font-medium">寄样</th>
                <th className="py-2 pr-2 font-medium">佣金 %（下限 / 上限）</th>
                <th className="py-2 pr-2 font-medium">报价有效期至</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.market_code} className={r.enabled ? "" : "text-slate-400"}>
                  <td className="py-1.5 pr-2 whitespace-nowrap">
                    <label className="flex items-center gap-2">
                      <input type="checkbox" checked={r.enabled} onChange={(e) => setRow(r.market_code, { enabled: e.target.checked })} />
                      <span className="font-medium">{r.market_code}</span>
                      <span className="text-xs">{currency.get(r.market_code)}</span>
                    </label>
                  </td>
                  <td className="py-1.5 pr-2">
                    <Select
                      disabled={!r.enabled}
                      value={r.price_status}
                      onChange={(e) => setRow(r.market_code, { price_status: e.target.value as "known" | "unknown" })}
                    >
                      <option value="known">已知</option>
                      <option value="unknown">未知</option>
                    </Select>
                  </td>
                  <td className="py-1.5 pr-2">
                    <Input
                      disabled={!r.enabled || r.price_status !== "known"}
                      inputMode="decimal"
                      placeholder="19.99"
                      value={r.price_status === "known" ? (r.price_amount ?? "") : ""}
                      onChange={(e) => setRow(r.market_code, { price_amount: e.target.value })}
                      className="w-24"
                    />
                  </td>
                  <td className="py-1.5 pr-2">
                    <Select
                      disabled={!r.enabled}
                      value={r.sample_policy ?? "unknown"}
                      onChange={(e) => setRow(r.market_code, { sample_policy: e.target.value as TermRow["sample_policy"] })}
                    >
                      {Object.entries(samplePolicyLabels).map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="py-1.5 pr-2">
                    <div className="flex gap-1">
                      <Input
                        disabled={!r.enabled}
                        inputMode="decimal"
                        className="w-16"
                        value={r.commission_min_pct ?? ""}
                        onChange={(e) => setRow(r.market_code, { commission_min_pct: e.target.value })}
                      />
                      <Input
                        disabled={!r.enabled}
                        inputMode="decimal"
                        className="w-16"
                        value={r.commission_max_pct ?? ""}
                        onChange={(e) => setRow(r.market_code, { commission_max_pct: e.target.value })}
                      />
                    </div>
                  </td>
                  <td className="py-1.5 pr-2">
                    <Input
                      type="date"
                      disabled={!r.enabled}
                      value={r.quote_valid_until ?? ""}
                      onChange={(e) => setRow(r.market_code, { quote_valid_until: e.target.value })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
