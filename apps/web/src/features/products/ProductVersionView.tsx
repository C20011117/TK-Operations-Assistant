import { Badge } from "@/components/ui";
import type { ProductVersion } from "@/lib/api/types";
import { fmtTime, money, samplePolicyLabels, versionStatusLabels } from "@/lib/labels";

function List({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm">
        {items.length ? (
          <ul className="list-inside list-disc space-y-0.5">
            {items.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        ) : (
          <span className="text-slate-400">未填写</span>
        )}
      </dd>
    </div>
  );
}

/** 只读展示一个产品版本（当前版本或历史版本）。 */
export function ProductVersionView({ v }: { v: ProductVersion }) {
  const f = v.facts;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-500">
        <Badge tone={v.status === "confirmed" ? "green" : v.status === "draft" ? "blue" : "slate"}>
          v{v.version_no} · {versionStatusLabels[v.status]}
        </Badge>
        <span>确认于 {fmtTime(v.confirmed_at)}</span>
      </div>
      <p className="text-sm">{f.summary || <span className="text-slate-400">未填写简介</span>}</p>
      <p className="mt-1 text-sm">
        <span className="text-slate-500">TikTok 商品类目：</span>
        {f.category ? f.category.path : <span className="text-amber-700">未设置</span>}
      </p>
      <dl className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <List label="主要卖点" items={f.selling_points ?? []} />
        <List label="使用场景" items={f.use_scenarios ?? []} />
        <List label="禁用表述" items={f.forbidden_claims ?? []} />
        <List label="参考链接" items={f.reference_links ?? []} />
        <div>
          <dt className="text-xs text-slate-500">目标客户</dt>
          <dd className="mt-1 text-sm">{f.target_customers || <span className="text-slate-400">未填写</span>}</dd>
        </div>
        {f.notes && (
          <div>
            <dt className="text-xs text-slate-500">备注</dt>
            <dd className="mt-1 text-sm whitespace-pre-wrap">{f.notes}</dd>
          </div>
        )}
      </dl>
      <div>
        <h3 className="mb-2 text-sm font-medium">各站点价格条款</h3>
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 text-slate-500">
            <tr>
              <th className="py-1.5 pr-3 font-medium">站点</th>
              <th className="py-1.5 pr-3 font-medium">售价</th>
              <th className="py-1.5 pr-3 font-medium">寄样</th>
              <th className="py-1.5 pr-3 font-medium">佣金</th>
              <th className="py-1.5 pr-3 font-medium">报价有效期</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {v.market_terms.map((t) => (
              <tr key={t.market_code}>
                <td className="py-1.5 pr-3 font-medium">{t.market_code}</td>
                <td className="py-1.5 pr-3">
                  {t.price_status === "known" ? money(t.price_amount, t.price_currency) : <Badge tone="amber">未知</Badge>}
                </td>
                <td className="py-1.5 pr-3">{samplePolicyLabels[t.sample_policy ?? "unknown"]}</td>
                <td className="py-1.5 pr-3">
                  {t.commission_min_pct || t.commission_max_pct
                    ? `${t.commission_min_pct ?? "?"}% – ${t.commission_max_pct ?? "?"}%`
                    : "—"}
                </td>
                <td className="py-1.5 pr-3">{t.quote_valid_until ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
