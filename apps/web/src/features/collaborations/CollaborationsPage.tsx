import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { Badge, Button, Empty, ErrorText, PageHeader } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { collabStatusLabels, deliveryLabels, fmtTime, shipmentStatusLabels } from "@/lib/labels";

type Status = keyof typeof collabStatusLabels;
const FILTERS: (Status | "active")[] = ["active", "planned", "contacting", "negotiating", "agreed", "in_progress", "closed"];
export const collabTone = {
  planned: "slate",
  contacting: "blue",
  negotiating: "blue",
  agreed: "green",
  in_progress: "green",
  completed: "slate",
  closed: "slate",
} as const;

export function CollaborationsPage() {
  const [filter, setFilter] = useState<Status | "active">("active");
  const q = useQuery({
    queryKey: ["collaborations", filter],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/collaborations", {
          params: { query: filter === "active" ? {} : { status: filter } },
        }),
      ),
  });
  const rows = (q.data ?? []).filter((r) => filter !== "active" || !["closed", "completed"].includes(r.status));

  return (
    <div className="space-y-4">
      <PageHeader title="我的合作" subtitle="从推荐卡“准备合作”进入；记录双方约定后才能安排寄样。" />
      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <Button key={f} variant={f === filter ? "primary" : "secondary"} onClick={() => setFilter(f)}>
            {f === "active" ? "进行中" : collabStatusLabels[f]}
          </Button>
        ))}
      </div>
      {q.error && <ErrorText>{errorMessage(q.error)}</ErrorText>}
      {q.data && rows.length === 0 && <Empty>没有合作。在找人任务的推荐卡上标记“保留”后点“准备合作”。</Empty>}
      {rows.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2">达人</th>
                <th className="px-4 py-2">任务 / 站点</th>
                <th className="px-4 py-2">状态</th>
                <th className="px-4 py-2">寄样</th>
                <th className="px-4 py-2">下一步</th>
                <th className="px-4 py-2">更新</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50">
                  <td className="px-4 py-2">
                    <Link className="font-medium text-sky-700 hover:underline" to={`/collaborations/${r.id}`}>
                      {r.creator.nickname ?? r.creator.unique_id}
                    </Link>
                    {r.creator.unique_id && <span className="ml-1 text-xs text-slate-500">@{r.creator.unique_id}</span>}
                  </td>
                  <td className="px-4 py-2">
                    {r.campaign_name} <span className="text-xs text-slate-500">· {r.market_code} · {r.product_name} v{r.product_version_no}</span>
                  </td>
                  <td className="px-4 py-2">
                    <Badge tone={collabTone[r.status]}>{collabStatusLabels[r.status]}</Badge>
                  </td>
                  <td className="px-4 py-2 text-xs">
                    {r.shipment_status ? shipmentStatusLabels[r.shipment_status as keyof typeof shipmentStatusLabels] : "—"}
                    {r.delivery_status && ` · ${deliveryLabels[r.delivery_status as keyof typeof deliveryLabels]}`}
                  </td>
                  <td className="px-4 py-2 text-slate-700">{r.next_step ?? "—"}</td>
                  <td className="px-4 py-2 text-xs text-slate-500">{fmtTime(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
