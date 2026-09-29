import { useQuery } from "@tanstack/react-query";
import { NavLink, Outlet } from "react-router";

import { api } from "@/lib/api/client";

const nav = [
  { to: "/", label: "我的工作台", end: true },
  { to: "/products", label: "产品档案", end: false },
  { to: "/campaigns", label: "找人任务", end: false },
  { to: "/markets", label: "站点目录", end: false },
  { to: "/system", label: "系统状态", end: false },
  { to: "/settings", label: "设置", end: false },
];

export function AppLayout() {
  const info = useQuery({
    queryKey: ["system", "info"],
    queryFn: async () => (await api.GET("/api/v1/system/info")).data ?? null,
    staleTime: Infinity,
  });

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
          <span className="text-xs text-slate-400">{info.data ? `v${info.data.version}` : ""}</span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}
