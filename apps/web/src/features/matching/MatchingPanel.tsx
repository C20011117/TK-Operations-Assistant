import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Badge, Button, Card, Empty, ErrorText, Select } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey, unwrap } from "@/lib/api/client";
import type { CampaignDetail, CampaignMarketView, RunView } from "@/lib/api/types";
import { callStatusLabels, fmtTime, groupLabels, runStatusLabels, stageLabels } from "@/lib/labels";

import { RecommendationCardView } from "./RecommendationCardView";

const ACTIVE = ["queued", "running"];
const statusTone = {
  queued: "slate",
  running: "blue",
  succeeded: "green",
  partial: "amber",
  failed: "red",
  cancelled: "slate",
} as const;

function RunProgress({ run }: { run: RunView }) {
  const qc = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/matching-runs/{run_id}/cancel", { params: { path: { run_id: run.id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs", run.campaign_market_id] }),
  });
  const k = run.counters as Record<string, number | undefined>;
  const pct = typeof k.percent === "number" ? k.percent : undefined;
  const active = ACTIVE.includes(run.status);
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone[run.status]}>{runStatusLabels[run.status]}</Badge>
        {active && run.stage && <span className="text-slate-600">{stageLabels[run.stage] ?? run.stage}</span>}
        <span className="text-slate-500">
          {run.source === "manual_import" ? "人工导入" : `FastMoss 地区 ${run.provider_region}`} · 条件 v{run.criteria_version_no} · 产品 v
          {run.product_version_no}
        </span>
        <span className="text-slate-500">{fmtTime(run.created_at)}</span>
        {active && (
          <Button variant="ghost" className="ml-auto" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            取消
          </Button>
        )}
      </div>
      {active && (
        <div className="h-1.5 w-full overflow-hidden rounded bg-slate-100">
          <div className="h-full bg-sky-500 transition-all" style={{ width: `${pct ?? 5}%` }} />
        </div>
      )}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-slate-600">
        {run.source === "fastmoss" && (
          <span>
            FastMoss 额度：已用 {run.credits_used}
            {run.cost_cap_credits != null && ` / 上限 ${run.cost_cap_credits}`}
          </span>
        )}
        {k.candidates != null && <span>候选 {k.candidates}</span>}
        {k.qualified != null && (
          <span>
            合格 {k.qualified} · 待核实 {k.needs_verification ?? 0} · 排除 {k.excluded ?? 0}
          </span>
        )}
        {k.assess_total != null && (
          <span>
            AI 判断 {k.assessed ?? 0}/{k.assess_total}
            {k.assess_failed ? `（失败 ${k.assess_failed}）` : ""}
          </span>
        )}
        {run.llm_input_tokens + run.llm_output_tokens > 0 && (
          <span>模型用量 {run.llm_input_tokens + run.llm_output_tokens} tokens</span>
        )}
      </div>
      {run.stop_message && run.stop_reason !== "pool_reached" && run.stop_reason !== "exhausted" && (
        <p className={run.status === "partial" ? "text-amber-700" : "text-slate-600"}>{run.stop_message}</p>
      )}
      {run.error && <ErrorText>{String((run.error as { message?: string }).message ?? "运行失败")}</ErrorText>}
      {cancel.isError && <ErrorText>{errorMessage(cancel.error)}</ErrorText>}
    </div>
  );
}

function CallsTable({ run }: { run: RunView }) {
  if (!run.calls?.length) return null;
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-slate-500">FastMoss 调用记录（{run.calls.length} 次）</summary>
      <table className="mt-2 w-full text-left">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1 pr-3 font-medium">关键词</th>
            <th className="py-1 pr-3 font-medium">页</th>
            <th className="py-1 pr-3 font-medium">结果</th>
            <th className="py-1 pr-3 font-medium">条数 / 总数</th>
            <th className="py-1 pr-3 font-medium">扣费</th>
            <th className="py-1 pr-3 font-medium">剩余额度</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {run.calls.map((c) => (
            <tr key={c.id}>
              <td className="py-1 pr-3">{c.keywords ?? "（无）"}</td>
              <td className="py-1 pr-3">{c.page}</td>
              <td className="py-1 pr-3" title={c.error ?? undefined}>
                {callStatusLabels[c.status] ?? c.status}
              </td>
              <td className="py-1 pr-3">
                {c.result_count ?? "—"} / {c.total ?? "—"}
              </td>
              <td className="py-1 pr-3">{c.credit_cost ?? "—"}</td>
              <td className="py-1 pr-3">{c.remaining_credits ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function Recommendations({ run }: { run: RunView }) {
  const [group, setGroup] = useState<keyof typeof groupLabels>("qualified");
  const done = !ACTIVE.includes(run.status);
  const q = useQuery({
    queryKey: ["recommendations", run.id, run.status],
    queryFn: () => unwrap(api.GET("/api/v1/matching-runs/{run_id}/recommendations", { params: { path: { run_id: run.id } } })),
    enabled: done,
  });
  if (!done) return null;
  if (q.isError) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  if (!q.data) return <p className="text-sm text-slate-500">加载推荐…</p>;
  const snap = q.data.snapshot;
  if (!snap) return <Empty>本次运行没有生成推荐名单{run.status === "cancelled" ? "（已取消）" : ""}。</Empty>;
  const cards = q.data.cards.filter((c) => c.group === group);
  return (
    <div className="space-y-3">
      {snap.limitations.length > 0 && (
        <ul className="space-y-1 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {snap.limitations.map((l) => (
            <li key={l}>· {l}</li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        {(Object.keys(groupLabels) as (keyof typeof groupLabels)[]).map((g) => (
          <Button key={g} variant={g === group ? "primary" : "secondary"} onClick={() => setGroup(g)}>
            {groupLabels[g]} {snap.counts[g] ?? 0}
          </Button>
        ))}
      </div>
      {group === "needs_verification" && cards.length > 0 && (
        <p className="text-xs text-slate-500">这些候选至少有一条硬条件数据未知，核实前不计入合格名单。</p>
      )}
      {group === "excluded" && cards.length > 0 && (
        <p className="text-xs text-slate-500">不满足硬条件的候选只作解释，不会因为软条件得分高而重新进入名单。</p>
      )}
      {cards.length === 0 ? (
        <Empty>该分组没有候选。</Empty>
      ) : (
        <div className="space-y-2">
          {cards.map((c) => (
            <RecommendationCardView key={c.evaluation_id} card={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function ManualImport({ cm, onDone }: { cm: CampaignMarketView; onDone: (r: RunView) => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const keyRef = useRef(newIdempotencyKey());
  const imp = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/campaign-markets/{cm_id}/manual-import", {
          params: { path: { cm_id: cm.id }, header: { "Idempotency-Key": keyRef.current } },
          body: { csv: file!.text, filename: file!.name },
        }),
      ),
    onSuccess: (r) => {
      keyRef.current = newIdempotencyKey();
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      onDone(r);
    },
  });
  return (
    <div className="space-y-2 rounded-md border border-dashed border-slate-300 p-3 text-sm">
      <p className="text-slate-600">
        导入 CSV 候选名单（UTF-8）。必须有“用户名”列；可选列：昵称、粉丝数、近28天GMV、近28天销量、平均播放、互动率、有邮箱、币种。最多 500 行。
      </p>
      <div className="flex items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          onChange={async (e) => {
            const f = e.target.files?.[0];
            setFile(f ? { name: f.name, text: await f.text() } : null);
          }}
        />
        <Button variant="secondary" disabled={!file || imp.isPending || cm.status !== "ready"} onClick={() => imp.mutate()}>
          {imp.isPending ? "导入中…" : "导入并匹配"}
        </Button>
      </div>
      {imp.isError && <ErrorText>{errorMessage(imp.error)}</ErrorText>}
    </div>
  );
}

/** 找人任务页里的“匹配”区：启动 / 导入、进度、历史、推荐卡。 */
export function MatchingPanel({ c, cm }: { c: CampaignDetail; cm: CampaignMarketView }) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [showImport, setShowImport] = useState(false);
  const manualOnly = cm.market_data_status === "manual_import_only";
  const runs = useQuery({
    queryKey: ["runs", cm.id],
    queryFn: () => unwrap(api.GET("/api/v1/campaign-markets/{cm_id}/matching-runs", { params: { path: { cm_id: cm.id } } })),
    refetchInterval: (q) => (q.state.data?.some((r) => ACTIVE.includes(r.status)) ? 1500 : false),
  });
  const runId = selected ?? runs.data?.[0]?.id ?? null;
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => unwrap(api.GET("/api/v1/matching-runs/{run_id}", { params: { path: { run_id: runId! } } })),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1500 : false),
  });
  const keyRef = useRef(newIdempotencyKey());
  const start = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/campaign-markets/{cm_id}/matching-runs", {
          params: { path: { cm_id: cm.id }, header: { "Idempotency-Key": keyRef.current } },
        }),
      ),
    onSuccess: (r) => after(r),
  });
  function after(r: RunView) {
    keyRef.current = newIdempotencyKey();
    setSelected(r.id);
    setShowImport(false);
    qc.invalidateQueries({ queryKey: ["runs", cm.id] });
  }
  const run = detail.data;
  const anyActive = runs.data?.some((r) => ACTIVE.includes(r.status));
  const canStart = cm.status === "ready" && c.status === "active" && !anyActive;

  return (
    <Card
      title="匹配与推荐"
      actions={
        <div className="flex items-center gap-2">
          {!manualOnly && (
            <Button variant="ghost" onClick={() => setShowImport(!showImport)}>
              人工导入
            </Button>
          )}
          {!manualOnly && (
            <Button disabled={!canStart || start.isPending} onClick={() => start.mutate()}>
              {start.isPending ? "启动中…" : "启动匹配"}
            </Button>
          )}
        </div>
      }
    >
      <div className="space-y-4">
        {cm.status !== "ready" && <p className="text-sm text-slate-500">任务确认后才能启动匹配。</p>}
        {manualOnly ? (
          <>
            <p className="text-sm text-amber-800">
              {cm.market_name_zh}没有 FastMoss 数据，只能人工导入候选名单（非实时数据）。导入后按同样的条件判断并生成推荐卡。
            </p>
            <ManualImport cm={cm} onDone={after} />
          </>
        ) : (
          <>
            <p className="text-xs text-slate-500">
              FastMoss 每次搜索约 1 额度、最多 10 人，空结果不扣费。本任务上限 {cm.cost_cap_credits ?? "未设置"} 额度，达到上限自动停止。
              英国任务查询时自动使用地区码 GB。
            </p>
            {showImport && <ManualImport cm={cm} onDone={after} />}
          </>
        )}
        {start.isError && <ErrorText>{errorMessage(start.error)}</ErrorText>}
        {runs.data && runs.data.length > 1 && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-500">历史运行：</span>
            <Select className="w-80" value={runId ?? ""} onChange={(e) => setSelected(e.target.value)}>
              {runs.data.map((r) => (
                <option key={r.id} value={r.id}>
                  {fmtTime(r.created_at)} · {runStatusLabels[r.status]} · {r.source === "manual_import" ? "导入" : `额度 ${r.credits_used}`}
                </option>
              ))}
            </Select>
          </div>
        )}
        {runs.data?.length === 0 && <Empty>还没有匹配记录。</Empty>}
        {run && (
          <>
            <RunProgress run={run} />
            <CallsTable run={run} />
            <Recommendations run={run} />
          </>
        )}
      </div>
    </Card>
  );
}
