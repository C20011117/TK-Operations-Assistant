import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Button } from "@/components/ui";
import { getBackend } from "@/lib/api/backend";

/** 等待本机后端就绪；失败时给出可理解的提示和重试按钮。 */
export function BackendGate({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ["backend"], queryFn: getBackend, retry: false, staleTime: Infinity });

  if (q.isPending) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-slate-500">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900" />
        <p className="text-sm">正在启动…</p>
      </div>
    );
  }
  if (q.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
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
