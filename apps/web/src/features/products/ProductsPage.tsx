import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Badge, Button, Card, Empty, ErrorText, Field, Input, PageHeader } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import { fmtTime } from "@/lib/labels";

export function ProductsPage() {
  const nav = useNavigate();
  const [showArchived, setShowArchived] = useState(false);
  const [form, setForm] = useState<{ sku: string; name: string } | null>(null);
  const list = useQuery({
    queryKey: ["products", showArchived],
    queryFn: () => unwrap(api.GET("/api/v1/products", { params: { query: { include_archived: showArchived } } })),
  });
  const create = useMutation({
    mutationFn: (body: { sku: string; name: string }) => unwrap(api.POST("/api/v1/products", { body })),
    onSuccess: (p) => nav(`/products/${p.id}`),
  });

  return (
    <div>
      <PageHeader
        title="产品档案"
        subtitle="产品资料按版本保存：修改会生成草稿，确认后成为新版本；已建的找人任务继续使用当时的版本。"
        actions={<Button onClick={() => setForm({ sku: "", name: "" })}>新建产品</Button>}
      />
      {form && (
        <Card title="新建产品">
          <form
            className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_2fr_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(form);
            }}
          >
            <Field label="SKU / 型号" hint="用于区分具体型号，不能与在用产品重复">
              <Input value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} autoFocus />
            </Field>
            <Field label="产品名称">
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <div className="flex items-end gap-2">
              <Button type="submit" disabled={!form.sku.trim() || !form.name.trim() || create.isPending}>
                创建并填写资料
              </Button>
              <Button type="button" variant="ghost" onClick={() => setForm(null)}>
                取消
              </Button>
            </div>
          </form>
          {create.isError && <ErrorText>{errorMessage(create.error)}</ErrorText>}
        </Card>
      )}
      <div className="mt-6">
        <Card
          actions={
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              显示已归档
            </label>
          }
        >
          {list.isError && <ErrorText>{errorMessage(list.error)}</ErrorText>}
          {list.data?.length === 0 && <Empty>还没有产品。先新建一个产品并确认资料，才能创建找人任务。</Empty>}
          {!!list.data?.length && (
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">产品</th>
                  <th className="py-2 pr-4 font-medium">SKU</th>
                  <th className="py-2 pr-4 font-medium">当前版本</th>
                  <th className="py-2 pr-4 font-medium">已定价站点</th>
                  <th className="py-2 pr-4 font-medium">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {list.data.map((p) => (
                  <tr key={p.id} className="hover:bg-slate-50">
                    <td className="py-2.5 pr-4">
                      <Link to={`/products/${p.id}`} className="font-medium hover:underline">
                        {p.name}
                      </Link>
                      {p.status === "archived" && (
                        <span className="ml-2">
                          <Badge>已归档</Badge>
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4 font-mono text-xs">{p.sku}</td>
                    <td className="py-2.5 pr-4">
                      {p.current_version_no ? `v${p.current_version_no}` : <Badge tone="amber">未确认</Badge>}
                      {p.has_draft && (
                        <span className="ml-2">
                          <Badge tone="blue">有草稿</Badge>
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4 text-slate-600">{p.market_codes.join(" ") || "—"}</td>
                    <td className="py-2.5 pr-4 text-slate-500">{fmtTime(p.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
