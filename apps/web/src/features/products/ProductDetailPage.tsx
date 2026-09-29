import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { Badge, Button, Card, ErrorText, Field, Input, PageHeader } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { ProductDetail } from "@/lib/api/types";
import { fmtTime, versionStatusLabels } from "@/lib/labels";

import { DraftEditor } from "./DraftEditor";
import { ProductVersionView } from "./ProductVersionView";

function HistoryVersion({ id }: { id: string }) {
  const q = useQuery({
    queryKey: ["product-version", id],
    queryFn: () => unwrap(api.GET("/api/v1/products/versions/{version_id}", { params: { path: { version_id: id } } })),
    staleTime: Infinity, // 已确认的版本不会再变
  });
  if (q.isPending) return <p className="text-sm text-slate-500">加载中…</p>;
  if (q.isError) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  return <ProductVersionView v={q.data} />;
}

export function ProductDetailPage() {
  const { productId = "" } = useParams();
  const qc = useQueryClient();
  const [viewing, setViewing] = useState<string | null>(null);
  const [editName, setEditName] = useState<{ sku: string; name: string } | null>(null);
  const q = useQuery({
    queryKey: ["product", productId],
    queryFn: () => unwrap(api.GET("/api/v1/products/{product_id}", { params: { path: { product_id: productId } } })),
  });
  const set = (d: ProductDetail) => {
    qc.setQueryData(["product", productId], d);
    qc.invalidateQueries({ queryKey: ["products"] });
  };
  const path = { params: { path: { product_id: productId } } };
  const startDraft = useMutation({ mutationFn: () => unwrap(api.POST("/api/v1/products/{product_id}/draft", path)), onSuccess: set });
  const rename = useMutation({
    mutationFn: (body: { sku: string; name: string }) => unwrap(api.PUT("/api/v1/products/{product_id}", { ...path, body })),
    onSuccess: (d) => {
      set(d);
      setEditName(null);
    },
  });
  const archive = useMutation({
    mutationFn: (archived: boolean) =>
      unwrap(archived ? api.POST("/api/v1/products/{product_id}/archive", path) : api.POST("/api/v1/products/{product_id}/restore", path)),
    onSuccess: set,
  });

  if (q.isPending) return <p className="text-sm text-slate-500">加载中…</p>;
  if (q.isError) return <ErrorText>{errorMessage(q.error)}</ErrorText>;
  const p = q.data;
  const archived = p.status === "archived";

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {p.name}
            <span className="font-mono text-sm font-normal text-slate-500">{p.sku}</span>
            {archived && <Badge>已归档</Badge>}
          </span>
        }
        subtitle={
          <Link to="/products" className="hover:underline">
            ← 产品档案
          </Link>
        }
        actions={
          <>
            {!archived && (
              <Button variant="secondary" onClick={() => setEditName({ sku: p.sku, name: p.name })}>
                改名称 / SKU
              </Button>
            )}
            {!archived && p.current && (
              <Link to={`/campaigns/new?product=${p.id}`}>
                <Button>用此产品建找人任务</Button>
              </Link>
            )}
            <Button variant="ghost" onClick={() => archive.mutate(!archived)}>
              {archived ? "取消归档" : "归档"}
            </Button>
          </>
        }
      />
      {(archive.error || startDraft.error) && <ErrorText>{errorMessage(archive.error ?? startDraft.error)}</ErrorText>}

      {editName && (
        <Card title="修改名称与 SKU">
          <form
            className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_2fr_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              rename.mutate(editName);
            }}
          >
            <Field label="SKU">
              <Input value={editName.sku} onChange={(e) => setEditName({ ...editName, sku: e.target.value })} />
            </Field>
            <Field label="名称">
              <Input value={editName.name} onChange={(e) => setEditName({ ...editName, name: e.target.value })} />
            </Field>
            <div className="flex items-end gap-2">
              <Button type="submit" disabled={rename.isPending}>
                保存
              </Button>
              <Button type="button" variant="ghost" onClick={() => setEditName(null)}>
                取消
              </Button>
            </div>
          </form>
          {rename.isError && <ErrorText>{errorMessage(rename.error)}</ErrorText>}
        </Card>
      )}

      {p.draft && !archived && <DraftEditor key={p.draft.id + p.draft.content_hash} product={p} draft={p.draft} />}

      <Card
        title="当前版本"
        actions={
          p.current && !p.draft && !archived ? (
            <Button variant="secondary" onClick={() => startDraft.mutate()} disabled={startDraft.isPending}>
              修改资料（生成新草稿）
            </Button>
          ) : undefined
        }
      >
        {p.current ? <ProductVersionView v={p.current} /> : <p className="text-sm text-slate-500">还没有确认的版本。填好上方草稿后点“确认”。</p>}
      </Card>

      <Card title="版本历史">
        <ul className="divide-y divide-slate-100 text-sm">
          {p.versions.map((v) => (
            <li key={v.id} className="py-2">
              <div className="flex items-center justify-between gap-4">
                <span className="flex items-center gap-3">
                  <span className="font-medium">v{v.version_no}</span>
                  <Badge tone={v.status === "confirmed" ? "green" : v.status === "draft" ? "blue" : "slate"}>
                    {versionStatusLabels[v.status]}
                  </Badge>
                  <span className="text-slate-500">{v.confirmed_at ? `确认于 ${fmtTime(v.confirmed_at)}` : `创建于 ${fmtTime(v.created_at)}`}</span>
                </span>
                {v.status === "superseded" && (
                  <Button variant="ghost" onClick={() => setViewing(viewing === v.id ? null : v.id)}>
                    {viewing === v.id ? "收起" : "查看"}
                  </Button>
                )}
              </div>
              {viewing === v.id && (
                <div className="mt-3 rounded-md bg-slate-50 p-4">
                  <HistoryVersion id={v.id} />
                </div>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
