import { useState } from "react";

import { Badge } from "@/components/ui";
import type { CardNote, CardPoint, RecommendationCard } from "@/lib/api/types";
import { compact, fitLabels } from "@/lib/labels";

function MetricCell({ m }: { m: RecommendationCard["metrics"][string] }) {
  const isMoney = m.currency !== undefined && m.label.includes("GMV");
  let value: string;
  if (m.value == null) value = "未知";
  else if (isMoney) value = `${compact(m.value)} ${m.currency ?? "（币种未知）"}`;
  else value = m.label === "互动率" ? `${m.value}%` : compact(m.value);
  return (
    <div className="min-w-0" title={m.note || undefined}>
      <div className="text-xs text-slate-500">{m.label}</div>
      <div className={`text-sm font-medium ${m.value == null ? "text-slate-400" : ""}`}>
        {value}
        {m.state === "anomaly" && (
          <span className="ml-1">
            <Badge tone="amber">异常</Badge>
          </span>
        )}
        {m.state === "inconsistent" && (
          <span className="ml-1">
            <Badge tone="amber">矛盾</Badge>
          </span>
        )}
      </div>
    </div>
  );
}

function Points({ title, tone, items }: { title: string; tone: "green" | "red"; items: CardPoint[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className={`mb-1 text-xs font-semibold ${tone === "green" ? "text-emerald-700" : "text-rose-700"}`}>{title}</div>
      <ul className="space-y-1 text-sm">
        {items.map((p, i) => (
          <li key={i} className="flex flex-wrap items-baseline gap-1.5">
            <span className="text-xs text-slate-400">{p.source === "ai" ? "AI" : (p.tag ?? "规则")}</span>
            <span>{p.text}</span>
            {p.evidence?.map((e) => (
              <span key={e.key} className="rounded bg-slate-100 px-1.5 text-xs text-slate-600" title="引用的数据">
                {e.label}：{e.value}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Notes({ title, className, items }: { title: string; className: string; items: (CardNote | string)[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className={`mb-1 text-xs font-semibold ${className}`}>{title}</div>
      <ul className="list-inside list-disc space-y-0.5 text-sm text-slate-700">
        {items.map((n, i) => (
          <li key={i}>{typeof n === "string" ? n : n.text}</li>
        ))}
      </ul>
    </div>
  );
}

/** 推荐卡：同时显示匹配点、不匹配点、未知项、异常项、待确认问题。 */
export function RecommendationCardView({ card }: { card: RecommendationCard }) {
  const [open, setOpen] = useState(card.group !== "excluded");
  const c = card.creator;
  const fit = card.ai.product_fit;
  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button type="button" className="flex w-full items-center gap-3 px-4 py-3 text-left" onClick={() => setOpen(!open)}>
        <span className="w-6 text-sm text-slate-400">{card.rank}</span>
        <span className="min-w-0 flex-1">
          <span className="font-medium">{c.nickname ?? c.unique_id}</span>
          {c.unique_id && <span className="ml-2 text-sm text-slate-500">@{c.unique_id}</span>}
          {card.ai.summary && <span className="block truncate text-sm text-slate-600">{card.ai.summary}</span>}
        </span>
        {fit && <Badge tone={fit === "high" ? "green" : fit === "low" ? "red" : "slate"}>{fitLabels[fit]}</Badge>}
        {card.ai.status === "failed" && <Badge tone="amber">AI 判断失败</Badge>}
        {card.anomalies.length > 0 && <Badge tone="amber">数据异常 {card.anomalies.length}</Badge>}
        {card.questions.length > 0 && <Badge tone="blue">待确认 {card.questions.length}</Badge>}
        <span className="text-slate-400">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="space-y-4 border-t border-slate-100 px-4 py-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            {Object.entries(card.metrics).map(([k, m]) => (
              <MetricCell key={k} m={m} />
            ))}
            <div>
              <div className="text-xs text-slate-500">联系邮箱</div>
              <div className="text-sm font-medium">{card.has_email == null ? "未知" : card.has_email ? "有" : "无"}</div>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Points title="匹配点" tone="green" items={card.matches} />
            <Points title="不匹配点" tone="red" items={card.mismatches} />
            <Notes title="未知项" className="text-slate-600" items={card.unknowns} />
            <Notes title="异常项" className="text-amber-700" items={card.anomalies} />
            <Notes title="待确认问题" className="text-sky-700" items={card.questions} />
            {!!card.ai.unsupported?.length && (
              <Notes title="模型推断（没有数据支持，仅供参考）" className="text-slate-400" items={card.ai.unsupported} />
            )}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
            {c.profile_url && (
              <span className="select-all" title="复制到浏览器打开">
                {c.profile_url}
              </span>
            )}
            {c.region && <span>达人所在地区：{c.region}</span>}
            {card.soft_score != null && <span>软条件得分：{card.soft_score}</span>}
            {card.categories && <span>{card.categories}</span>}
            {card.profile_text && <span className="truncate">简介：{card.profile_text}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
