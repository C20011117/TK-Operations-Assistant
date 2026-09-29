import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge, Button, Card, ErrorText } from "@/components/ui";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api/client";
import type { JobView } from "@/lib/api/types";

const jobLabel: Record<JobView["status"], { text: string; tone: "green" | "amber" | "red" | "slate" | "blue" }> = {
  queued: { text: "排队中", tone: "slate" },
  running: { text: "执行中", tone: "blue" },
  waiting_input: { text: "等待输入", tone: "amber" },
  cancel_requested: { text: "取消中", tone: "amber" },
  succeeded: { text: "已完成", tone: "green" },
  partial: { text: "部分完成", tone: "amber" },
  failed: { text: "失败", tone: "red" },
  cancelled: { text: "已取消", tone: "slate" },
};

function HealthCard() {
  const health = useQuery({
    queryKey: ["system", "health"],
    queryFn: async () => (await api.GET("/api/v1/system/health")).data ?? null,
    refetchInterval: 15_000,
  });
  const info = useQuery({
    queryKey: ["system", "info"],
    queryFn: async () => (await api.GET("/api/v1/system/info")).data ?? null,
    staleTime: Infinity,
  });
  const db = health.data?.checks.database;
  return (
    <Card title="本机服务">
      <ul className="space-y-2 text-sm">
        <li className="flex justify-between">
          <span>本地数据库</span>
          {health.isPending ? (
            <span className="text-slate-500">检查中…</span>
          ) : (
            <Badge tone={db === "ok" ? "green" : "red"}>{db === "ok" ? "正常" : (db ?? "不可用")}</Badge>
          )}
        </li>
        <li className="flex justify-between">
          <span>版本</span>
          <span className="text-slate-600">{info.data?.version ?? "—"}</span>
        </li>
        <li className="flex justify-between gap-4">
          <span className="shrink-0">数据目录</span>
          <span className="truncate font-mono text-xs text-slate-600" title={info.data?.data_dir}>
            {info.data?.data_dir ?? "—"}
          </span>
        </li>
      </ul>
    </Card>
  );
}

function JobsCard() {
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => (await api.GET("/api/v1/system/jobs")).data ?? [],
    refetchInterval: (query) =>
      query.state.data?.some((j) => ["queued", "running", "cancel_requested"].includes(j.status)) ? 1000 : false,
  });
  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/system/jobs", {
        params: { header: { "Idempotency-Key": newIdempotencyKey() } },
        body: { kind: "system.noop", params: { sleep_ms: 3000, message: "链路测试" } },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
  const cancel = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.POST("/api/v1/system/jobs/{job_id}/cancel", { params: { path: { job_id: id } } });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });

  return (
    <Card
      title="后台任务"
      actions={
        <Button onClick={() => create.mutate()} disabled={create.isPending}>
          {create.isPending ? "提交中…" : "创建测试任务"}
        </Button>
      }
    >
      <p className="mb-3 text-sm text-slate-500">
        任务保存在本地数据库，由应用内的执行器处理；应用关闭时未完成的任务会在下次启动时自动恢复。
      </p>
      {create.isError && <ErrorText>{errorMessage(create.error)}</ErrorText>}
      <ul className="divide-y divide-slate-100 text-sm">
        {jobs.data?.map((j) => {
          const label = jobLabel[j.status];
          const pct = (j.progress as { percent?: number } | null)?.percent;
          return (
            <li key={j.id} className="flex items-center justify-between gap-3 py-2">
              <span className="font-mono text-xs text-slate-500">{j.id.slice(0, 8)}</span>
              <span className="text-slate-600">{j.kind}</span>
              <span className="text-slate-500">{new Date(j.created_at).toLocaleTimeString()}</span>
              <span className="w-12 text-right text-slate-500 tabular-nums">{pct != null ? `${pct}%` : ""}</span>
              <Badge tone={label.tone}>{label.text}</Badge>
              {["queued", "running"].includes(j.status) ? (
                <Button variant="ghost" onClick={() => cancel.mutate(j.id)}>
                  取消
                </Button>
              ) : (
                <span className="w-[52px]" />
              )}
            </li>
          );
        })}
        {jobs.data?.length === 0 && <li className="py-2 text-slate-500">还没有任务</li>}
      </ul>
    </Card>
  );
}

export function SystemPage() {
  return (
    <div className="grid grid-cols-1 gap-6">
      <HealthCard />
      <JobsCard />
    </div>
  );
}
