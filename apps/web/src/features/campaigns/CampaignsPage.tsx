import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import { Badge, Button, Card, Empty, ErrorText, PageHeader } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { goalLabels } from "@/lib/labels";

export function CampaignsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const q = useQuery({
    queryKey: ["campaigns", showArchived],
    queryFn: () => unwrap(api.GET("/api/v1/campaigns", { params: { query: { include_archived: showArchived } } })),
  });
  return (
    <div>
      <PageHeader
        title="找人任务"
        subtitle="一个任务 = 一个产品 + 一个欧洲站点。同一产品在不同站点分别建任务，条件和名单互不影响。"
        actions={
          <Link to="/campaigns/new">
            <Button>新建任务</Button>
          </Link>
        }
      />
      <Card
        actions={
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            显示已归档
          </label>
        }
      >
        {q.isError && <ErrorText>{errorMessage(q.error)}</ErrorText>}
        {q.data?.length === 0 && <Empty>还没有找人任务。</Empty>}
        {!!q.data?.length && (
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-medium">任务</th>
                <th className="py-2 pr-4 font-medium">产品</th>
                <th className="py-2 pr-4 font-medium">站点</th>
                <th className="py-2 pr-4 font-medium">目的</th>
                <th className="py-2 pr-4 font-medium">时间（站点当地）</th>
                <th className="py-2 pr-4 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {q.data.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50">
                  <td className="py-2.5 pr-4">
                    <Link to={`/campaigns/${c.id}`} className="font-medium hover:underline">
                      {c.name}
                    </Link>
                  </td>
                  <td className="py-2.5 pr-4 text-slate-600">{c.product_name}</td>
                  <td className="py-2.5 pr-4">
                    {c.market_name_zh} <span className="text-xs text-slate-500">{c.market_code} · {c.reporting_currency}</span>
                  </td>
                  <td className="py-2.5 pr-4 text-slate-600">{goalLabels[c.goal]}</td>
                  <td className="py-2.5 pr-4 text-slate-600">
                    {c.start_date ?? "?"} ~ {c.end_date ?? "?"}
                  </td>
                  <td className="py-2.5 pr-4">
                    {c.status === "archived" ? (
                      <Badge>已归档</Badge>
                    ) : c.market_status === "ready" ? (
                      <Badge tone="green">已确认</Badge>
                    ) : (
                      <Badge tone="amber">待完善</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
