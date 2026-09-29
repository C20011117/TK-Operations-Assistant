import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Badge, Button, Card, ErrorText, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { CampaignDetail, CampaignMarketView } from "@/lib/api/types";
import { collabLabels, fmtTime, goalLabels, localNow, money, operatorLabels, samplePolicyLabels } from "@/lib/labels";

import { MatchingPanel } from "@/features/matching/MatchingPanel";

import { CriteriaEditor, type Row, useFieldSpecs } from "./CriteriaEditor";

function useSetCampaign(id: string) {
  const qc = useQueryClient();
  return (d: CampaignDetail) => {
    qc.setQueryData(["campaign", id], d);
    qc.invalidateQueries({ queryKey: ["campaigns"] });
  };
}

function ReadinessCard({ c, cm }: { c: CampaignDetail; cm: CampaignMarketView }) {
  const set = useSetCampaign(c.id);
  const specs = useFieldSpecs();
  const [asking, setAsking] = useState(false);
  const confirm = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/campaign-markets/{cm_id}/confirm", { params: { path: { cm_id: cm.id } } })),
    onSuccess: (d) => {
      set(d);
      setAsking(false);
    },
  });
  const r = cm.readiness;
  const pending = cm.criteria_draft ?? cm.criteria_current;
  const hard = (pending?.criteria ?? []).filter((x) => x.hardness === "hard");
  const label = (k: string) => specs.data?.find((s) => s.key === k)?.label ?? k;
  const archived = c.status === "archived";

  return (
    <Card
      title="就绪检查"
      actions={
        cm.status === "ready" ? (
          <Badge tone="green">已确认 · {fmtTime(cm.confirmed_at)}</Badge>
        ) : (
          <Button disabled={!r.ready || archived} onClick={() => setAsking(true)}>
            确认任务
          </Button>
        )
      }
    >
      {r.blockers.length === 0 && r.warnings.length === 0 && <p className="text-sm text-emerald-700">没有缺项。</p>}
      <ul className="space-y-1.5 text-sm">
        {r.blockers.map((b) => (
          <li key={b.code} className="flex gap-2">
            <Badge tone="red">必须补充</Badge>
            {b.message}
          </li>
        ))}
        {r.warnings.map((w) => (
          <li key={w.code} className="flex gap-2">
            <Badge tone="amber">提醒</Badge>
            {w.message}
          </li>
        ))}
      </ul>
      {cm.status === "ready" && (
        <p className="mt-3 text-sm text-slate-500">任务已确认，可以在下方“匹配与推荐”中启动找人。修改设置或条件后需要重新确认。</p>
      )}
      {asking && (
        <div className="mt-4 rounded-md border border-slate-300 bg-slate-50 p-4 text-sm">
          <p className="font-medium">确认后，条件将冻结为新版本，并固定使用产品 v{cm.product_version_no}。</p>
          {hard.length ? (
            <>
              <p className="mt-2">以下硬条件会直接决定候选是否合格，请确认它们来自企业的真实要求：</p>
              <ul className="mt-1 list-inside list-disc">
                {hard.map((h) => (
                  <li key={h.criterion_id}>
                    {label(h.field_key)} {operatorLabels[h.operator]} {JSON.stringify(h.expected)}（未知时{h.unknown_policy === "exclude" ? "排除" : "待核实"}）
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="mt-2">没有硬条件：所有搜到的候选都会进入推荐，由软条件排序。</p>
          )}
          <div className="mt-3 flex gap-2">
            <Button onClick={() => confirm.mutate()} disabled={confirm.isPending}>
              {confirm.isPending ? "确认中…" : "确认"}
            </Button>
            <Button variant="ghost" onClick={() => setAsking(false)}>
              取消
            </Button>
          </div>
          {confirm.isError && <ErrorText>{errorMessage(confirm.error)}</ErrorText>}
        </div>
      )}
    </Card>
  );
}

function SettingsCard({ c, cm }: { c: CampaignDetail; cm: CampaignMarketView }) {
  const set = useSetCampaign(c.id);
  const [f, setF] = useState({
    target_list_size: cm.target_list_size?.toString() ?? "",
    budget_min: cm.budget_min ?? "",
    budget_max: cm.budget_max ?? "",
    cost_cap_credits: cm.cost_cap_credits?.toString() ?? "",
  });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/campaign-markets/{cm_id}", {
          params: { path: { cm_id: cm.id } },
          body: {
            target_list_size: f.target_list_size ? Number(f.target_list_size) : null,
            budget_min: f.budget_min || null,
            budget_max: f.budget_max || null,
            cost_cap_credits: f.cost_cap_credits ? Number(f.cost_cap_credits) : null,
          },
        }),
      ),
    onSuccess: set,
  });
  const manual = cm.market_data_status === "manual_import_only";
  const dirty =
    f.target_list_size !== (cm.target_list_size?.toString() ?? "") ||
    f.budget_min !== (cm.budget_min ?? "") ||
    f.budget_max !== (cm.budget_max ?? "") ||
    f.cost_cap_credits !== (cm.cost_cap_credits?.toString() ?? "");
  const k = (key: keyof typeof f) => ({ value: f[key], onChange: (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [key]: e.target.value }) });

  return (
    <Card
      title="名单与费用"
      actions={
        <Button variant="secondary" disabled={!dirty || save.isPending || c.status === "archived"} onClick={() => save.mutate()}>
          保存
        </Button>
      }
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Field label="目标名单数量（必填）">
          <Input inputMode="numeric" {...k("target_list_size")} placeholder="30" />
        </Field>
        <Field label={`合作预算下限（${cm.reporting_currency}）`}>
          <Input inputMode="decimal" {...k("budget_min")} />
        </Field>
        <Field label={`合作预算上限（${cm.reporting_currency}）`} hint="样品、运费、坑位费等，不含佣金">
          <Input inputMode="decimal" {...k("budget_max")} />
        </Field>
        <Field
          label={manual ? "FastMoss 额度上限（此站点不需要）" : "FastMoss 额度上限（必填）"}
          hint="超过后找人任务自动停止；每次搜索约 1 额度 / 10 人"
        >
          <Input inputMode="numeric" disabled={manual} {...k("cost_cap_credits")} placeholder="60" />
        </Field>
      </div>
      {save.isError && <ErrorText>{errorMessage(save.error)}</ErrorText>}
    </Card>
  );
}

function CriteriaCard({ c, cm }: { c: CampaignDetail; cm: CampaignMarketView }) {
  const set = useSetCampaign(c.id);
  const base = cm.criteria_draft ?? cm.criteria_current;
  const [rows, setRows] = useState<Row[]>(() => (base?.criteria ?? []) as Row[]);
  const [keywords, setKeywords] = useState((base?.search.keywords ?? []).join("，"));
  const [dirty, setDirty] = useState(false);
  const archived = c.status === "archived";

  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/v1/campaign-markets/{cm_id}/criteria", {
          params: { path: { cm_id: cm.id } },
          body: {
            criteria: rows as never,
            search: { keywords: keywords.split(/[,，\n]/).map((s) => s.trim()).filter(Boolean) },
          },
        }),
      ),
    onSuccess: (d) => {
      set(d);
      setDirty(false);
    },
  });
  const discard = useMutation({
    mutationFn: () => unwrap(api.DELETE("/api/v1/campaign-markets/{cm_id}/criteria", { params: { path: { cm_id: cm.id } } })),
    onSuccess: (d) => {
      set(d);
      const cur = d.markets[0]?.criteria_current;
      setRows((cur?.criteria ?? []) as Row[]);
      setKeywords((cur?.search.keywords ?? []).join("，"));
      setDirty(false);
    },
  });

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          找人条件
          {cm.criteria_current && <Badge tone="green">生效 v{cm.criteria_current.version_no}</Badge>}
          {cm.criteria_draft && <Badge tone="blue">草稿 v{cm.criteria_draft.version_no}</Badge>}
        </span>
      }
      actions={
        !archived && (
          <div className="flex gap-2">
            {cm.criteria_draft && cm.criteria_current && (
              <Button variant="ghost" onClick={() => discard.mutate()} disabled={discard.isPending}>
                放弃草稿
              </Button>
            )}
            <Button variant="secondary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? "保存中…" : "保存条件"}
            </Button>
          </div>
        )
      }
    >
      <div className="mb-4">
        <Field label="搜索关键词" hint="最多 5 个，用逗号分隔；例如产品名、品类、内容主题（英语站点建议用英文）">
          <Input
            disabled={archived}
            value={keywords}
            onChange={(e) => {
              setKeywords(e.target.value);
              setDirty(true);
            }}
            placeholder="portable blender, smoothie"
          />
        </Field>
      </div>
      <CriteriaEditor
        rows={rows}
        onChange={(r) => {
          setRows(r);
          setDirty(true);
        }}
        currency={cm.reporting_currency}
        marketLanguages={cm.content_languages}
        disabled={archived}
      />
      <p className="mt-3 text-xs text-slate-500">
        硬条件：不满足就不合格；数据未知时按所选方式处理。软条件：只影响排序和推荐理由。“需人工核实”的字段 FastMoss 没有数据，候选在核实前都是“未知”。
      </p>
      {(save.error || discard.error) && <ErrorText>{errorMessage(save.error ?? discard.error)}</ErrorText>}
    </Card>
  );
}

function InfoCard({ c, cm }: { c: CampaignDetail; cm: CampaignMarketView }) {
  const set = useSetCampaign(c.id);
  const nav = useNavigate();
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({
    name: c.name,
    goal: c.goal,
    collaboration_type: c.collaboration_type,
    start_date: c.start_date ?? "",
    end_date: c.end_date ?? "",
    notes: c.notes,
  });
  const [dupMarket, setDupMarket] = useState("");
  const markets = useQuery({
    queryKey: ["markets", "europe"],
    queryFn: () => unwrap(api.GET("/api/v1/markets", { params: { query: { region_group: "europe" } } })),
    staleTime: Infinity,
  });
  const path = { params: { path: { campaign_id: c.id } } };
  const save = useMutation({
    mutationFn: () =>
      unwrap(api.PUT("/api/v1/campaigns/{campaign_id}", { ...path, body: { ...f, start_date: f.start_date || null, end_date: f.end_date || null } })),
    onSuccess: (d) => {
      set(d);
      setEdit(false);
    },
  });
  const useLatest = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/campaign-markets/{cm_id}/use-latest-product", { params: { path: { cm_id: cm.id } } })),
    onSuccess: set,
  });
  const dup = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/campaigns/{campaign_id}/duplicate", { ...path, body: { market_code: dupMarket } })),
    onSuccess: (d) => nav(`/campaigns/${d.id}`),
  });
  const archive = useMutation({
    mutationFn: () =>
      unwrap(c.status === "archived" ? api.POST("/api/v1/campaigns/{campaign_id}/restore", path) : api.POST("/api/v1/campaigns/{campaign_id}/archive", path)),
    onSuccess: set,
  });
  const outdated = cm.latest_product_version_no != null && cm.latest_product_version_no !== cm.product_version_no;
  const p = cm.price_term;
  const set2 = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: e.target.value });

  return (
    <Card
      title="任务信息"
      actions={
        c.status === "active" && (
          <Button variant="ghost" onClick={() => setEdit(!edit)}>
            {edit ? "收起" : "编辑"}
          </Button>
        )
      }
    >
      {!edit ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
          <div>
            <dt className="text-xs text-slate-500">站点</dt>
            <dd>
              {cm.market_name_zh}（{cm.market_code}
              {cm.fastmoss_region && cm.fastmoss_region !== cm.market_code ? ` · FastMoss ${cm.fastmoss_region}` : ""}）
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">币种 / 时区</dt>
            <dd>
              {cm.reporting_currency} · {cm.time_zone}
              <span className="block text-xs text-slate-500">当地现在 {localNow(cm.time_zone)}</span>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">时间（站点当地日期）</dt>
            <dd>
              {c.start_date ?? "未定"} ~ {c.end_date ?? "未定"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-500">目的 / 合作形式</dt>
            <dd>
              {goalLabels[c.goal]} · {collabLabels[c.collaboration_type]}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-slate-500">产品资料</dt>
            <dd className="flex flex-wrap items-center gap-2">
              <Link to={`/products/${c.product.id}`} className="hover:underline">
                {c.product.name}
              </Link>
              <Badge>使用 v{cm.product_version_no}</Badge>
              {outdated && (
                <>
                  <Badge tone="amber">产品已有 v{cm.latest_product_version_no}</Badge>
                  {c.status === "active" && (
                    <Button variant="secondary" onClick={() => useLatest.mutate()} disabled={useLatest.isPending}>
                      更新到 v{cm.latest_product_version_no}
                    </Button>
                  )}
                </>
              )}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-xs text-slate-500">该站点价格条款（v{cm.product_version_no}）</dt>
            <dd>
              {p ? (
                <>
                  售价 {p.price_status === "known" ? money(p.price_amount, p.price_currency) : "未知"} · 寄样{" "}
                  {samplePolicyLabels[p.sample_policy ?? "unknown"]}
                  {(p.commission_min_pct || p.commission_max_pct) && ` · 佣金 ${p.commission_min_pct ?? "?"}%–${p.commission_max_pct ?? "?"}%`}
                </>
              ) : (
                <span className="text-rose-600">缺少，请到产品档案补充后更新版本</span>
              )}
            </dd>
          </div>
          {c.notes && (
            <div className="col-span-full">
              <dt className="text-xs text-slate-500">备注</dt>
              <dd className="whitespace-pre-wrap">{c.notes}</dd>
            </div>
          )}
        </dl>
      ) : (
        <form
          className="grid grid-cols-1 gap-4 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <Field label="任务名称">
            <Input value={f.name} onChange={set2("name")} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="推广目的">
              <Select value={f.goal} onChange={set2("goal")}>
                {Object.entries(goalLabels).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="合作形式">
              <Select value={f.collaboration_type} onChange={set2("collaboration_type")}>
                {Object.entries(collabLabels).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field label="开始日期">
              <Input type="date" value={f.start_date} onChange={set2("start_date")} />
            </Field>
            <Field label="结束日期">
              <Input type="date" value={f.end_date} onChange={set2("end_date")} />
            </Field>
          </div>
          <Field label="备注">
            <Textarea rows={2} value={f.notes} onChange={set2("notes")} />
          </Field>
          <div className="flex justify-end gap-2 md:col-span-2">
            {save.isError && <ErrorText>{errorMessage(save.error)}</ErrorText>}
            <Button type="submit" disabled={save.isPending}>
              保存
            </Button>
          </div>
        </form>
      )}
      {useLatest.isError && <ErrorText>{errorMessage(useLatest.error)}</ErrorText>}
      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4 text-sm">
        <span className="text-slate-500">复制到其他站点：</span>
        <Select className="w-44" value={dupMarket} onChange={(e) => setDupMarket(e.target.value)}>
          <option value="">选择站点</option>
          {markets.data
            ?.filter((m) => m.market_code !== cm.market_code)
            .map((m) => (
              <option key={m.market_code} value={m.market_code}>
                {m.name_zh}（{m.market_code}）
              </option>
            ))}
        </Select>
        <Button variant="secondary" disabled={!dupMarket || dup.isPending} onClick={() => dup.mutate()}>
          复制
        </Button>
        <span className="text-xs text-slate-500">预算、金额类和内容语言条件不会复制（币种、语言不同）</span>
        <Button variant="ghost" className="ml-auto" onClick={() => archive.mutate()}>
          {c.status === "archived" ? "取消归档" : "归档任务"}
        </Button>
      </div>
      {(dup.error || archive.error) && <ErrorText>{errorMessage(dup.error ?? archive.error)}</ErrorText>}
    </Card>
  );
}

function HistoryCard({ cm }: { cm: CampaignMarketView }) {
  return (
    <Card title="条件版本">
      <ul className="divide-y divide-slate-100 text-sm">
        {cm.criteria_history.map((h) => (
          <li key={h.id} className="flex items-center gap-3 py-2">
            <span className="font-medium">v{h.version_no}</span>
            <Badge tone={h.status === "confirmed" ? "green" : h.status === "draft" ? "blue" : "slate"}>
              {h.status === "confirmed" ? "生效" : h.status === "draft" ? "草稿" : "历史"}
            </Badge>
            <span className="text-slate-500">产品 v{h.product_version_no}</span>
            <span className="text-slate-500">{h.confirmed_at ? `确认于 ${fmtTime(h.confirmed_at)}` : "未确认"}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function CampaignDetailPage() {
  const { campaignId = "" } = useParams();
  const q = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => unwrap(api.GET("/api/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: campaignId } } })),
  });
  if (q.isPending) return <p className="text-sm text-slate-500">加载中…</p>;
  if (q.isError) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  const c = q.data;
  const cm = c.markets[0];
  if (!cm) return <ErrorText>任务缺少站点数据</ErrorText>;
  // 条件编辑器以当前草稿/生效版本为初始值；版本变化时重新挂载
  const criteriaKey = `${(cm.criteria_draft ?? cm.criteria_current)?.id}-${cm.product_version_id}`;

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {c.name}
            {c.status === "archived" ? (
              <Badge>已归档</Badge>
            ) : cm.status === "ready" ? (
              <Badge tone="green">已确认</Badge>
            ) : (
              <Badge tone="amber">待完善</Badge>
            )}
          </span>
        }
        subtitle={
          <Link to="/campaigns" className="hover:underline">
            ← 找人任务
          </Link>
        }
      />
      <ReadinessCard c={c} cm={cm} />
      <MatchingPanel c={c} cm={cm} />
      <InfoCard key={c.updated_at} c={c} cm={cm} />
      <SettingsCard key={`s-${cm.target_list_size}-${cm.budget_min}-${cm.budget_max}-${cm.cost_cap_credits}`} c={c} cm={cm} />
      <CriteriaCard key={criteriaKey} c={c} cm={cm} />
      <HistoryCard cm={cm} />
    </div>
  );
}
