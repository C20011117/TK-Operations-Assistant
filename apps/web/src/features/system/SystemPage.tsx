import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { useCurrent } from "@/app/useCurrent";
import { Badge, Button, Card, ErrorText } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api/client";
import type { ExternalChecks, JobView } from "@/lib/api/types";

const jobTone: Record<JobView["status"], "green" | "amber" | "red" | "slate" | "blue"> = {
  queued: "slate",
  running: "blue",
  waiting_input: "amber",
  cancel_requested: "amber",
  succeeded: "green",
  partial: "amber",
  failed: "red",
  cancelled: "slate",
};

function HealthCard() {
  const q = useQuery({
    queryKey: ["health", "ready"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/health/ready");
      return data ?? (error as unknown as { checks?: Record<string, string> }) ?? null;
    },
    refetchInterval: 15_000,
  });
  const checks = (q.data as { checks?: Record<string, string> } | null)?.checks ?? {};
  return (
    <Card title="服务依赖">
      <ul className="space-y-2 text-sm">
        {Object.entries(checks).map(([k, v]) => (
          <li key={k} className="flex justify-between">
            <span>{k === "database" ? "数据库" : k === "redis" ? "Redis 队列" : k}</span>
            <Badge tone={v === "ok" ? "green" : "red"}>{v === "ok" ? "正常" : v}</Badge>
          </li>
        ))}
        {q.isPending && <li className="text-slate-500">检查中…</li>}
      </ul>
    </Card>
  );
}

function JobsCard() {
  const { current } = useCurrent();
  const qc = useQueryClient();
  const tenant_id = current.tenant_id;
  const jobs = useQuery({
    queryKey: ["jobs", tenant_id],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/tenants/{tenant_id}/system/jobs", { params: { path: { tenant_id } } });
      return data ?? [];
    },
    refetchInterval: (query) =>
      query.state.data?.some((j) => j.status === "queued" || j.status === "running") ? 1000 : false,
  });
  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/tenants/{tenant_id}/system/jobs", {
        params: { path: { tenant_id }, header: { "Idempotency-Key": newIdempotencyKey() } },
        body: { kind: "system.noop", params: { sleep_ms: 1500, message: "M0 链路测试" } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", tenant_id] }),
  });

  return (
    <Card
      title="后台任务链路"
      actions={
        current.role !== "viewer" && (
          <Button onClick={() => create.mutate()} disabled={create.isPending}>
            {create.isPending ? "提交中…" : "创建测试任务"}
          </Button>
        )
      }
    >
      <p className="mb-3 text-sm text-slate-500">API → Outbox → 分发器 → Celery Worker。任务只对创建者本人可见。</p>
      {create.isError && <ErrorText>{errorMessage(create.error)}</ErrorText>}
      <ul className="divide-y divide-slate-100 text-sm">
        {jobs.data?.map((j) => (
          <li key={j.id} className="flex items-center justify-between py-2">
            <span className="font-mono text-xs text-slate-500">{j.id.slice(0, 8)}</span>
            <span className="text-slate-600">{j.kind}</span>
            <span className="text-slate-500">{new Date(j.created_at).toLocaleTimeString()}</span>
            <Badge tone={jobTone[j.status]}>{j.status}</Badge>
          </li>
        ))}
        {jobs.data?.length === 0 && <li className="py-2 text-slate-500">还没有任务</li>}
      </ul>
    </Card>
  );
}

function ExternalChecksCard() {
  const { current } = useCurrent();
  const [result, setResult] = useState<ExternalChecks | null>(null);
  const run = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/tenants/{tenant_id}/system/checks", {
        params: { path: { tenant_id: current.tenant_id } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (d) => setResult(d ?? null),
  });
  if (current.role !== "admin") return null;
  const row = (label: string, c?: ExternalChecks["llm"]) => (
    <li className="flex items-start justify-between gap-4 py-2">
      <span className="font-medium">{label}</span>
      {c ? (
        <span className="text-right">
          <Badge tone={c.status === "ok" ? "green" : c.status === "not_configured" ? "amber" : "red"}>
            {c.status === "ok" ? "正常" : c.status === "not_configured" ? "未配置" : "失败"}
          </Badge>
          <span className="mt-1 block text-xs text-slate-500">
            {c.available_credits != null && `可用额度 ${c.available_credits} · 本次扣费 ${c.credit_cost ?? 0} · `}
            {c.model && `模型 ${c.model} · `}
            {c.latency_ms != null && `${c.latency_ms} ms · `}
            {c.detail}
          </span>
        </span>
      ) : (
        <span className="text-slate-400">未检查</span>
      )}
    </li>
  );
  return (
    <Card
      title="外部服务连通"
      actions={
        <Button variant="secondary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "检查中…" : "运行检查"}
        </Button>
      }
    >
      <p className="mb-2 text-sm text-slate-500">FastMoss 使用免费的余额查询，不扣额度；LLM 发送一条极短测试消息。</p>
      {run.isError && <ErrorText>{errorMessage(run.error)}</ErrorText>}
      <ul className="divide-y divide-slate-100 text-sm">
        {row("FastMoss MCP", result?.fastmoss)}
        {row("大模型（OpenAI 兼容）", result?.llm)}
      </ul>
    </Card>
  );
}

export function SystemPage() {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <HealthCard />
      <ExternalChecksCard />
      <div className="lg:col-span-2">
        <JobsCard />
      </div>
    </div>
  );
}
