import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "@/components/ui";
import { getBackend } from "@/lib/api/backend";

/** 启动期间的内容区占位：与页面布局相近的灰色块，避免整屏转圈。 */
function Skeleton() {
  return (
    <div className="animate-pulse space-y-6" aria-busy="true" aria-label="正在准备数据">
      <div className="space-y-2">
        <div className="h-6 w-40 rounded bg-slate-200" />
        <div className="h-4 w-72 rounded bg-slate-100" />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
            <div className="h-4 w-24 rounded bg-slate-200" />
            <div className="h-3 w-full rounded bg-slate-100" />
            <div className="h-3 w-2/3 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** 等待本机后端就绪（内容区内）；失败时给出可理解的提示和重试按钮。 */
export function BackendGate({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ["backend"], queryFn: getBackend, retry: false, staleTime: Infinity });

  if (q.isPending) {
    return <Skeleton />;
  }
  if (q.isError) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center px-6">
        <div className="max-w-md space-y-3 text-center">
          <h1 className="text-lg font-semibold">后台服务没有启动成功</h1>
          <p className="text-sm break-all text-slate-600">{String(q.error)}</p>
          <p className="text-xs text-slate-500">日志位置：%LOCALAPPDATA%\TKWorkspace\logs\backend.log</p>
          <Button onClick={() => q.refetch()}>重试</Button>
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
