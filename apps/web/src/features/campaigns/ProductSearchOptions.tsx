import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, ErrorText, Input, Select } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { Competitor, SearchInput } from "@/lib/api/types";

export type SearchOpts = Required<Omit<SearchInput, "keywords">>;

export function searchOptsFrom(s: SearchInput | undefined | null): SearchOpts {
  return {
    use_product_category: s?.use_product_category ?? true,
    category_level: s?.category_level ?? "l3",
    competitors: s?.competitors ?? [],
    enrich_competitor_creators: s?.enrich_competitor_creators ?? true,
  };
}

const MAX_COMPETITORS = 3;

/** 按产品找达人：类目搜索开关 + 竞品（卖过竞品的达人优先进入候选）。 */
export function ProductSearchOptions({
  cmId,
  value,
  onChange,
  targetSize,
  disabled,
}: {
  cmId: string;
  value: SearchOpts;
  onChange: (v: SearchOpts) => void;
  targetSize: number | null | undefined;
  disabled: boolean;
}) {
  const [link, setLink] = useState("");
  const [kw, setKw] = useState("");
  const [confirming, setConfirming] = useState(false);
  const suggest = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/campaign-markets/{cm_id}/competitor-suggestions", {
          params: { path: { cm_id: cmId } },
          body: { keywords: kw.trim() || null },
        }),
      ),
    onSettled: () => setConfirming(false),
  });

  const comps = value.competitors;
  const full = comps.length >= MAX_COMPETITORS;
  const has = (id: string) => comps.some((c) => c.product_id === id);
  const add = (c: Competitor) => {
    if (full || has(c.product_id)) return;
    onChange({ ...value, competitors: [...comps, c] });
  };
  const enrichMax = Math.min(targetSize ?? 20, 20);
  const estimate = comps.length * 3 + (value.enrich_competitor_creators && comps.length ? enrichMax : 0);

  return (
    <div className="space-y-3 rounded-md border border-slate-200 p-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            disabled={disabled}
            checked={value.use_product_category}
            onChange={(e) => onChange({ ...value, use_product_category: e.target.checked })}
          />
          按产品的 TikTok 商品类目找“带过同类货”的达人
        </label>
        {value.use_product_category && (
          <Select
            disabled={disabled}
            className="w-44"
            value={value.category_level}
            onChange={(e) => onChange({ ...value, category_level: e.target.value as SearchOpts["category_level"] })}
          >
            <option value="l3">三级类目（最精准）</option>
            <option value="l2">二级类目（更宽）</option>
            <option value="l1">一级类目（最宽）</option>
          </Select>
        )}
        <span className="text-xs text-slate-500">类目在产品资料里设置；没设置时只按关键词搜索</span>
      </div>

      <div>
        <div className="mb-1.5 flex items-center gap-2 text-sm font-medium">
          竞品
          <span className="text-xs font-normal text-slate-500">
            卖过竞品的达人优先进入候选（每个竞品 3 额度，最多 {MAX_COMPETITORS} 个）
          </span>
        </div>
        {comps.length === 0 && <p className="text-sm text-slate-500">未添加竞品。</p>}
        <ul className="space-y-1">
          {comps.map((c) => (
            <li key={c.product_id} className="flex items-center gap-2 text-sm">
              <Badge tone="blue">竞品</Badge>
              <span className="truncate">{c.title || c.product_id}</span>
              <span className="text-xs text-slate-400">{c.product_id.length > 40 ? "链接" : c.product_id}</span>
              {!disabled && (
                <Button
                  variant="ghost"
                  onClick={() => onChange({ ...value, competitors: comps.filter((x) => x.product_id !== c.product_id) })}
                >
                  移除
                </Button>
              )}
            </li>
          ))}
        </ul>
        {!disabled && !full && (
          <div className="mt-2 flex flex-wrap gap-2">
            <Input
              className="w-80"
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="粘贴 TikTok 商品链接或商品编号"
            />
            <Button
              variant="secondary"
              disabled={!link.trim()}
              onClick={() => {
                add({ product_id: link.trim(), title: null, currency: null });
                setLink("");
              }}
            >
              添加
            </Button>
          </div>
        )}
        {!disabled && !full && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Input
              className="w-56"
              value={kw}
              onChange={(e) => setKw(e.target.value)}
              placeholder="可选：竞品关键词（英文更准）"
            />
            {confirming ? (
              <>
                <span className="text-xs text-amber-700">将消耗 1 个 FastMoss 额度，按本站点同类目近 28 天销量推荐</span>
                <Button onClick={() => suggest.mutate()} disabled={suggest.isPending}>
                  {suggest.isPending ? "查询中…" : "确认"}
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>
                  取消
                </Button>
              </>
            ) : (
              <Button variant="secondary" onClick={() => setConfirming(true)}>
                推荐热销竞品（1 额度）
              </Button>
            )}
          </div>
        )}
        {suggest.error && (
          <div className="mt-2">
            <ErrorText>{errorMessage(suggest.error)}</ErrorText>
          </div>
        )}
        {suggest.data && (
          <div className="mt-2 rounded-md bg-slate-50 p-2">
            <p className="mb-1 text-xs text-slate-500">
              {suggest.data.category_path ? `类目：${suggest.data.category_path}` : "按关键词"} · 消耗 {suggest.data.credits_used} 额度
            </p>
            {suggest.data.items.length === 0 && <p className="text-sm text-slate-500">没有找到商品。</p>}
            <ul className="divide-y divide-slate-200 text-sm">
              {suggest.data.items.map((s) => (
                <li key={s.product_id} className="flex items-center gap-2 py-1.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{s.title || s.product_id}</div>
                    <div className="text-xs text-slate-500">
                      {s.price_display ?? "价格未知"} · 近 28 天销量 {s.day28_units_sold ?? "—"} · 带货达人 {s.linked_creator_count ?? "—"}
                      {s.shop_name && ` · ${s.shop_name}`}
                    </div>
                  </div>
                  <Button
                    variant={has(s.product_id) ? "primary" : "ghost"}
                    disabled={disabled || has(s.product_id) || full}
                    onClick={() => add({ product_id: s.product_id, title: s.title, currency: s.currency })}
                  >
                    {has(s.product_id) ? "已添加" : "添加"}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {comps.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                disabled={disabled}
                checked={value.enrich_competitor_creators}
                onChange={(e) => onChange({ ...value, enrich_competitor_creators: e.target.checked })}
              />
              补全竞品达人的近 28 天带货数据（每人 1 额度，最多 {enrichMax} 人）
            </label>
            <span className="text-xs text-slate-500">竞品部分预计最多 {estimate} 额度，均受本站点额度上限约束</span>
          </div>
        )}
      </div>
    </div>
  );
}
