import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { Button, ErrorText, Input } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { CategorySuggestion, ProductCategory } from "@/lib/api/types";

/** TikTok 商品类目：用产品名称 / 品类词调用 FastMoss 类目识别（不扣费），用户从候选中选择。 */
export function CategoryPicker({
  value,
  onChange,
  defaultQuery,
}: {
  value: ProductCategory | null;
  onChange: (v: ProductCategory | null) => void;
  defaultQuery: string;
}) {
  const [query, setQuery] = useState(defaultQuery);
  const suggest = useMutation({
    mutationFn: () => {
      const words = query
        .split(/[,，;；\n]/)
        .map((w) => w.trim())
        .filter(Boolean)
        .slice(0, 5);
      return unwrap(api.POST("/api/v1/products/category-suggestions", { body: { query: words } }));
    },
  });
  const pick = (c: CategorySuggestion) =>
    onChange({ l1_id: c.l1_id, l2_id: c.l2_id ?? null, l3_id: c.l3_id ?? null, path: c.path ?? c.name ?? String(c.l1_id) });

  return (
    <div className="rounded-md border border-slate-200 p-3">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <span className="text-slate-500">当前类目：</span>
        {value ? (
          <>
            <span className="font-medium">{value.path}</span>
            <Button variant="ghost" onClick={() => onChange(null)}>
              清除
            </Button>
          </>
        ) : (
          <span className="text-amber-700">未设置（找人时无法按类目搜索）</span>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          className="w-72"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="产品名称或品类词，多个用逗号分隔，如：摄像头, security camera"
        />
        <Button variant="secondary" disabled={!query.trim() || suggest.isPending} onClick={() => suggest.mutate()}>
          {suggest.isPending ? "识别中…" : "推荐类目"}
        </Button>
        <span className="self-center text-xs text-slate-500">使用 FastMoss 类目识别，不消耗额度</span>
      </div>
      {suggest.error && (
        <div className="mt-2">
          <ErrorText>{errorMessage(suggest.error)}</ErrorText>
        </div>
      )}
      {suggest.data && (
        <ul className="mt-2 divide-y divide-slate-100 text-sm">
          {suggest.data.length === 0 && <li className="py-1.5 text-slate-500">没有匹配的类目，换个说法试试（英文品类词通常更准）</li>}
          {suggest.data.map((c) => {
            const selected = value?.l1_id === c.l1_id && value?.l2_id === c.l2_id && value?.l3_id === c.l3_id;
            return (
              <li key={`${c.l1_id}-${c.l2_id}-${c.l3_id}`} className="flex items-center justify-between gap-2 py-1.5">
                <span>
                  {c.path}
                  {c.matched_query && <span className="ml-2 text-xs text-slate-400">匹配“{c.matched_query}”</span>}
                </span>
                <Button variant={selected ? "primary" : "ghost"} onClick={() => pick(c)}>
                  {selected ? "已选择" : "选择"}
                </Button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
