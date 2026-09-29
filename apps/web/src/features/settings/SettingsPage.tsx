import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge, Button, Card, ErrorText, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api/client";
import type { ExternalCheck, ExternalChecks, SettingsView } from "@/lib/api/types";

const settingsKey = ["settings"] as const;

function useSettings() {
  return useQuery({
    queryKey: settingsKey,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/settings");
      if (error || !data) throw new Error("加载设置失败");
      return data;
    },
  });
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

function FastMossCard({ view }: { view: SettingsView }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const save = useMutation({
    mutationFn: async (api_key: string) => {
      const { data, error } = await api.PUT("/api/v1/settings/fastmoss", { body: { api_key } });
      if (error) throw error;
      return data;
    },
    onSuccess: (d) => {
      qc.setQueryData(settingsKey, d);
      setKey("");
    },
  });
  return (
    <Card
      title="FastMoss 数据"
      actions={
        <Badge tone={view.fastmoss.configured ? "green" : "amber"}>
          {view.fastmoss.configured ? `已配置 ${view.fastmoss.api_key_hint ?? ""}` : "未配置"}
        </Badge>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (key.trim()) save.mutate(key.trim());
        }}
      >
        <Field label="API Key" hint="以 fm_sk_ 开头，在 FastMoss 开发者后台创建。保存在 Windows 凭据管理器，不写入任何文件。">
          <Input
            type="password"
            autoComplete="off"
            placeholder={view.fastmoss.configured ? "已保存；输入新 Key 可替换" : "fm_sk_…"}
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </Field>
        {save.isError && <ErrorText>{errorMessage(save.error)}</ErrorText>}
        <div className="flex gap-2">
          <Button type="submit" disabled={!key.trim() || save.isPending}>
            保存
          </Button>
          {view.fastmoss.configured && (
            <Button type="button" variant="secondary" onClick={() => save.mutate("")} disabled={save.isPending}>
              删除 Key
            </Button>
          )}
        </div>
      </form>
    </Card>
  );
}

function LLMCard({ view }: { view: SettingsView }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    base_url: view.llm.base_url,
    model_matching: view.llm.model_matching,
    model_brief: view.llm.model_brief,
    structured_mode: view.llm.structured_mode,
    timeout_seconds: view.llm.timeout_seconds,
    data_region: view.llm.data_region,
  });
  const [key, setKey] = useState("");
  useEffect(() => {
    setForm({
      base_url: view.llm.base_url,
      model_matching: view.llm.model_matching,
      model_brief: view.llm.model_brief,
      structured_mode: view.llm.structured_mode,
      timeout_seconds: view.llm.timeout_seconds,
      data_region: view.llm.data_region,
    });
  }, [view]);
  const save = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.PUT("/api/v1/settings/llm", {
        body: { ...form, api_key: key.trim() ? key.trim() : null },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (d) => {
      qc.setQueryData(settingsKey, d);
      setKey("");
    },
  });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: k === "timeout_seconds" ? Number(e.target.value) : e.target.value }));

  return (
    <Card
      title="大模型（OpenAI 兼容接口）"
      actions={
        <Badge tone={view.llm.configured ? "green" : "amber"}>
          {view.llm.configured ? `已配置 ${view.llm.api_key_hint ?? ""}` : "未配置"}
        </Badge>
      }
    >
      <form
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="接口地址（Base URL）" hint="例如 https://api.openai.com/v1，或任意 OpenAI 兼容服务">
          <Input value={form.base_url} onChange={set("base_url")} placeholder="https://…/v1" />
        </Field>
        <Field label="API Key" hint="保存在 Windows 凭据管理器；留空表示不修改">
          <Input
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={view.llm.api_key_hint ? `已保存 ${view.llm.api_key_hint}` : "sk-…"}
          />
        </Field>
        <Field label="推荐判断模型" hint="必填">
          <Input value={form.model_matching} onChange={set("model_matching")} />
        </Field>
        <Field label="拍摄包生成模型" hint="留空则与推荐判断模型相同">
          <Input value={form.model_brief} onChange={set("model_brief")} />
        </Field>
        <Field label="结构化输出方式" hint="服务不支持 json_schema 时改为 function_calling 或 json_mode">
          <select
            className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm"
            value={form.structured_mode}
            onChange={set("structured_mode")}
          >
            <option value="json_schema">json_schema</option>
            <option value="function_calling">function_calling</option>
            <option value="json_mode">json_mode</option>
          </select>
        </Field>
        <Field label="超时（秒）">
          <Input type="number" min={5} max={600} value={form.timeout_seconds} onChange={set("timeout_seconds")} />
        </Field>
        <Field label="数据处理地区（记录用）" hint="例如 EU / US，用于 GDPR 记录">
          <Input value={form.data_region} onChange={set("data_region")} />
        </Field>
        <div className="flex items-end md:col-span-2">
          {save.isError && <ErrorText>{errorMessage(save.error)}</ErrorText>}
          <Button type="submit" disabled={save.isPending} className="ml-auto">
            {save.isPending ? "保存中…" : "保存"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function CheckRow({ label, c }: { label: string; c?: ExternalCheck }) {
  return (
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
}

function ChecksCard() {
  const [result, setResult] = useState<ExternalChecks | null>(null);
  const run = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/settings/checks");
      if (error) throw error;
      return data;
    },
    onSuccess: (d) => setResult(d ?? null),
  });
  return (
    <Card
      title="连通检查"
      actions={
        <Button variant="secondary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "检查中…" : "运行检查"}
        </Button>
      }
    >
      <p className="mb-2 text-sm text-slate-500">FastMoss 使用免费的余额查询，不扣额度；大模型发送一条极短的测试消息。</p>
      {run.isError && <ErrorText>{errorMessage(run.error)}</ErrorText>}
      <ul className="divide-y divide-slate-100 text-sm">
        <CheckRow label="FastMoss MCP" c={result?.fastmoss} />
        <CheckRow label="大模型" c={result?.llm} />
      </ul>
    </Card>
  );
}

export function SettingsPage() {
  const q = useSettings();
  if (q.isPending) return <p className="text-sm text-slate-500">加载中…</p>;
  if (q.isError || !q.data) return <ErrorText>{(q.error as Error)?.message ?? "加载设置失败"}</ErrorText>;
  return (
    <div className="grid grid-cols-1 gap-6">
      <FastMossCard view={q.data} />
      <LLMCard view={q.data} />
      <ChecksCard />
    </div>
  );
}
