import { useQuery } from "@tanstack/react-query";

import { Badge, Card, ErrorText } from "@/components/ui";
import { api } from "@/lib/api/client";
import type { Market } from "@/lib/api/types";

const statusView: Record<Market["data_status"], { label: string; tone: "green" | "amber" | "red" | "slate" }> = {
  queryable: { label: "可查询", tone: "green" },
  queryable_currency_unknown: { label: "可查询 · 币种未知", tone: "amber" },
  platform_unconfirmed: { label: "平台开放待确认", tone: "amber" },
  manual_import_only: { label: "仅人工导入", tone: "red" },
};

export function MarketsPage() {
  const q = useQuery({
    queryKey: ["markets", "europe"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/markets", { params: { query: { region_group: "europe" } } });
      if (error || !data) throw new Error("加载站点失败");
      return data;
    },
  });

  return (
    <Card title="欧洲站点目录">
      <p className="mb-4 text-sm text-slate-500">
        数据状态来自 2026-09-29 FastMoss MCP 实测。“可查询”只说明有数据，字段完整性在 M2 契约测试后确认。
      </p>
      {q.isError && <ErrorText>{(q.error as Error).message}</ErrorText>}
      {q.isPending ? (
        <p className="text-sm text-slate-500">加载中…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-medium">站点</th>
                <th className="py-2 pr-4 font-medium">FastMoss 地区码</th>
                <th className="py-2 pr-4 font-medium">结算币种</th>
                <th className="py-2 pr-4 font-medium">时区</th>
                <th className="py-2 pr-4 font-medium">语言</th>
                <th className="py-2 pr-4 font-medium">实测结果数</th>
                <th className="py-2 pr-4 font-medium">数据状态</th>
                <th className="py-2 font-medium">备注</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {q.data?.map((m) => {
                const s = statusView[m.data_status];
                return (
                  <tr key={m.market_code}>
                    <td className="py-2.5 pr-4">
                      <span className="font-medium">{m.name_zh}</span>{" "}
                      <span className="text-slate-400">{m.market_code}</span>
                    </td>
                    <td className="py-2.5 pr-4 font-mono">{m.fastmoss_region ?? "—"}</td>
                    <td className="py-2.5 pr-4">{m.settlement_currency}</td>
                    <td className="py-2.5 pr-4 text-slate-600">{m.default_time_zone}</td>
                    <td className="py-2.5 pr-4 text-slate-600">{m.content_languages.join(" / ")}</td>
                    <td className="py-2.5 pr-4 tabular-nums">{m.last_probe_total ?? "未知"}</td>
                    <td className="py-2.5 pr-4">
                      <Badge tone={s.tone}>{s.label}</Badge>
                    </td>
                    <td className="py-2.5 text-slate-500">{m.notes || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
