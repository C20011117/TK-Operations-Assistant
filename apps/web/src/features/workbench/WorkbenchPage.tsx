import { Badge, Card } from "@/components/ui";
import { useCurrent } from "@/app/useCurrent";

// 待办分组来自产品规格“核心界面建议”。M0 尚无业务数据，按里程碑标明何时开放，不显示虚构数字。
const groups = [
  { key: "to_ship", label: "待寄样", milestone: "M3" },
  { key: "to_brief", label: "待发拍摄包", milestone: "M4" },
  { key: "to_collect", label: "待回收视频", milestone: "M4" },
  { key: "to_review", label: "待审核", milestone: "M4" },
  { key: "to_revise", label: "待返修", milestone: "M4" },
  { key: "next_round", label: "待约下一条", milestone: "M5" },
  { key: "maintain", label: "待维护", milestone: "M5" },
];

export function WorkbenchPage() {
  const { me, current } = useCurrent();
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">你好，{me.display_name}</h1>
        <p className="mt-1 text-sm text-slate-500">
          {current.tenant_name} · 今天该推进谁，会按下面的分组列出来。
        </p>
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
