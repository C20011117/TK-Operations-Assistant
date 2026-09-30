import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { Badge, Card } from "@/components/ui";
import { useDueFollowUps } from "@/features/collaborations/followUp";
import { api, unwrap } from "@/lib/api/client";
import type { CollaborationSummary } from "@/lib/api/types";

// 待办分组来自产品规格“核心界面建议”。尚无业务数据，按里程碑标明何时开放，不显示虚构数字。
const groups = [
  { key: "to_brief", label: "待发拍摄包", milestone: "M4" },
  { key: "to_collect", label: "待回收视频", milestone: "M4" },
  { key: "to_review", label: "待审核", milestone: "M4" },
  { key: "to_revise", label: "待返修", milestone: "M4" },
  { key: "next_round", label: "待约下一条", milestone: "M5" },
  { key: "maintain", label: "待维护", milestone: "M5" },
];

function who(c: CollaborationSummary) {
  return c.creator.nickname ?? c.creator.unique_id ?? "达人";
}

function LiveList({ title, rows, empty, detail }: {
  title: string;
  rows: CollaborationSummary[] | undefined;
  empty: string;
  detail: (c: CollaborationSummary) => string;
}) {
  return (
    <Card>
      <div className="flex items-start justify-between">
        <span className="text-sm font-medium">{title}</span>
        <Badge tone={rows && rows.length > 0 ? "amber" : "slate"}>{rows ? rows.length : "…"}</Badge>
      </div>
      {rows && rows.length === 0 && <p className="mt-6 text-sm text-slate-400">{empty}</p>}
      <ul className="mt-3 space-y-2 text-sm">
        {rows?.slice(0, 6).map((c) => (
          <li key={c.id}>
            <Link to={`/collaborations/${c.id}`} className="font-medium text-sky-700 hover:underline">
              {who(c)}
            </Link>
            <span className="ml-1 text-xs text-slate-500">
              {c.market_code} · {c.product_name}
            </span>
            <div className="text-xs text-slate-600">{detail(c)}</div>
          </li>
        ))}
      </ul>
      {rows && rows.length > 6 && (
        <Link to="/collaborations" className="mt-2 block text-xs text-sky-700 hover:underline">
          还有 {rows.length - 6} 个 →
        </Link>
      )}
    </Card>
  );
}

export function WorkbenchPage() {
  const due = useDueFollowUps();
  const active = useQuery({
    queryKey: ["collaborations", "active"],
    queryFn: () => unwrap(api.GET("/api/v1/collaborations", { params: { query: {} } })),
  });
  const toShip = active.data?.filter(
    (c) => c.status === "agreed" && c.shipment_status !== "dispatched",
  );
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">我的工作台</h1>
        <p className="mt-1 text-sm text-slate-500">
          今天该推进谁，会按下面的分组列出来。首次使用请先到
          <Link to="/settings" className="mx-1 text-slate-900 underline">
            设置
          </Link>
          填写 FastMoss 与大模型的 API Key。
        </p>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <LiveList
          title="今天要跟进"
          rows={due.data}
          empty="没有到期的跟进。"
          detail={(c) => c.follow_up?.message ?? ""}
        />
        <LiveList title="待寄样" rows={toShip} empty="没有等待寄样的合作。" detail={(c) => c.next_step ?? ""} />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {groups.map((g) => (
          <Card key={g.key}>
            <div className="flex items-start justify-between">
              <span className="text-sm font-medium">{g.label}</span>
              <Badge>{g.milestone} 开放</Badge>
            </div>
            <p className="mt-6 text-sm text-slate-400">尚未开放</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
