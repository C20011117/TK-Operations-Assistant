import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router";

import { Badge, Button } from "@/components/ui";
import { useMe } from "@/features/auth/useMe";
import { api } from "@/lib/api/client";

const roleLabel: Record<string, string> = { admin: "管理员", bd: "BD", viewer: "只读" };

const nav = [
  { to: "/", label: "我的工作台", end: true },
  { to: "/markets", label: "站点目录", end: false },
  { to: "/system", label: "系统状态", end: false },
];

export function AppLayout() {
  const me = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();

  if (me.isPending) return <div className="p-8 text-sm text-slate-500">加载中…</div>;
  if (!me.data) return <Navigate to="/login" replace />;

  const logout = async () => {
    await api.POST("/api/v1/auth/logout");
    qc.clear();
    navigate("/login", { replace: true });
  };

  const current = me.data.current;
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <span className="font-semibold">TK 达人工作台</span>
            <nav className="flex gap-1">
              {nav.map((n) => (
                <NavLink
                  key={n.to}
                  to={n.to}
                  end={n.end}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`
                  }
                >
                  {n.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {current ? (
              <span className="text-slate-600">
                {current.tenant_name} · {me.data.display_name}{" "}
                <Badge tone="blue">{roleLabel[current.role] ?? current.role}</Badge>
              </span>
            ) : (
              <Badge tone="amber">未选择企业</Badge>
            )}
            <Button variant="ghost" onClick={logout}>
              退出
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        {current ? <Outlet context={{ me: me.data }} /> : <p className="text-sm text-slate-600">你的账号还没有加入任何企业。</p>}
      </main>
    </div>
  );
}
