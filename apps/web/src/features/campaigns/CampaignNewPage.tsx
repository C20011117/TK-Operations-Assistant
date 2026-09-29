import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { Button, Card, ErrorText, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { collabLabels, goalLabels, localNow } from "@/lib/labels";

export function CampaignNewPage() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const products = useQuery({ queryKey: ["products", false], queryFn: () => unwrap(api.GET("/api/v1/products")) });
  const markets = useQuery({
    queryKey: ["markets", "europe"],
    queryFn: () => unwrap(api.GET("/api/v1/markets", { params: { query: { region_group: "europe" } } })),
    staleTime: Infinity,
  });
  const [form, setForm] = useState({
    product_id: params.get("product") ?? "",
    market_code: "UK",
    name: "",
    goal: "sales" as keyof typeof goalLabels,
    collaboration_type: "free_sample" as keyof typeof collabLabels,
    start_date: "",
    end_date: "",
    notes: "",
  });
  const confirmed = products.data?.filter((p) => p.current_version_no) ?? [];
  const product = confirmed.find((p) => p.id === form.product_id);
  const market = markets.data?.find((m) => m.market_code === form.market_code);
  const hasTerm = product?.market_codes.includes(form.market_code);

  const create = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/campaigns", {
          body: {
            ...form,
            name: form.name || `${product?.name ?? ""} · ${market?.name_zh ?? form.market_code}`,
            start_date: form.start_date || null,
            end_date: form.end_date || null,
            market: {},
          },
        }),
      ),
    onSuccess: (c) => nav(`/campaigns/${c.id}`),
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <div>
      <PageHeader
        title="新建找人任务"
        subtitle={
          <Link to="/campaigns" className="hover:underline">
            ← 找人任务
          </Link>
        }
      />
      <Card>
        <form
          className="grid grid-cols-1 gap-4 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field
            label="产品"
            hint={
              confirmed.length === 0 ? (
                <>
                  没有已确认资料的产品，请先到
                  <Link to="/products" className="mx-1 underline">
                    产品档案
                  </Link>
                  确认。
                </>
              ) : (
                "任务会固定使用产品的当前确认版本"
              )
            }
          >
            <Select value={form.product_id} onChange={set("product_id")}>
              <option value="">请选择</option>
              {confirmed.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}（{p.sku} · v{p.current_version_no}）
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="销售站点"
            hint={
              market && (
                <>
                  结算币种 {market.settlement_currency} · 时区 {market.default_time_zone}（当地现在 {localNow(market.default_time_zone)}）
                  {market.data_status === "manual_import_only" && " · 没有 FastMoss 数据，只能人工导入候选"}
                  {product && !hasTerm && <span className="block text-amber-700">该产品当前版本没有此站点的价格条款，确认任务前需先补充</span>}
                </>
              )
            }
          >
            <Select value={form.market_code} onChange={set("market_code")}>
              {markets.data?.map((m) => (
                <option key={m.market_code} value={m.market_code}>
                  {m.name_zh}（{m.market_code}）
                </option>
              ))}
            </Select>
          </Field>
          <Field label="任务名称" hint="留空则自动用“产品 · 站点”">
            <Input value={form.name} onChange={set("name")} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="推广目的">
              <Select value={form.goal} onChange={set("goal")}>
                {Object.entries(goalLabels).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="合作形式">
              <Select value={form.collaboration_type} onChange={set("collaboration_type")}>
                {Object.entries(collabLabels).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="开始日期" hint={market && `站点当地日期（${market.default_time_zone}）`}>
              <Input type="date" value={form.start_date} onChange={set("start_date")} />
            </Field>
            <Field label="结束日期">
              <Input type="date" value={form.end_date} onChange={set("end_date")} />
            </Field>
          </div>
          <Field label="备注">
            <Textarea rows={2} value={form.notes} onChange={set("notes")} />
          </Field>
          <div className="flex items-end justify-end gap-3 md:col-span-2">
            {create.isError && <ErrorText>{errorMessage(create.error)}</ErrorText>}
            <Button type="submit" disabled={!form.product_id || create.isPending}>
              创建，下一步设置条件
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
