import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Badge, Button, ErrorText, Input, Select } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { CollaborationDetail, FollowUp } from "@/lib/api/types";
import { fmtTime } from "@/lib/labels";

/** 到期该跟进的合作（导航角标、工作台、列表筛选共用；每 5 分钟刷新一次）。 */
export function useDueFollowUps() {
  return useQuery({
    queryKey: ["collaborations", "due"],
    queryFn: () => unwrap(api.GET("/api/v1/collaborations", { params: { query: { due: true } } })),
    refetchInterval: 5 * 60 * 1000,
  });
}

export function FollowUpBadge({ f }: { f: FollowUp | null | undefined }) {
  if (!f) return null;
  if (f.overdue) return <Badge tone="amber">该跟进 · {f.idle_days} 天无进展</Badge>;
  return <span className="text-xs text-slate-400">下次跟进 {fmtTime(f.due_at).slice(0, 10)}</span>;
}

/** 详情页的跟进提醒条：已跟进（记一笔并重新计时）/ 稍后提醒 / 生成跟进话术。 */
export function FollowUpBar({
  c,
  onDraftFollowUp,
}: {
  c: CollaborationDetail;
  onDraftFollowUp: () => void;
}) {
  const qc = useQueryClient();
  const f = c.follow_up;
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [next, setNext] = useState("");
  const done = (d: CollaborationDetail) => {
    qc.setQueryData(["collaboration", c.id], d);
    qc.invalidateQueries({ queryKey: ["collaborations"] });
    setOpen(false);
    setNote("");
  };
  const record = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/follow-ups", {
          params: { path: { collab_id: c.id } },
          body: { note, next_in_days: next ? Number(next) : null },
        }),
      ),
    onSuccess: done,
  });
  const snooze = useMutation({
    mutationFn: (days: number) =>
      unwrap(
        api.POST("/api/v1/collaborations/{collab_id}/snooze", { params: { path: { collab_id: c.id } }, body: { days } }),
      ),
    onSuccess: done,
  });
  if (!f) return null;
  const err = record.error ?? snooze.error;
  return (
    <div className={`rounded-md p-3 text-sm ${f.overdue ? "bg-amber-50 ring-1 ring-amber-200" : "bg-slate-50"}`}>
      <div className="flex flex-wrap items-center gap-2">
        {f.overdue ? (
          <span className="font-medium text-amber-900">⏰ 该跟进了：{f.message}</span>
        ) : (
          <span className="text-slate-600">
            下次跟进：{fmtTime(f.due_at).slice(0, 10)}
            {f.source === "manual" ? "（手动设定）" : ""}
          </span>
        )}
        <span className="ml-auto flex flex-wrap gap-2">
          {f.suggest_follow_up_draft && (
            <Button variant="secondary" onClick={onDraftFollowUp}>
              生成跟进话术
            </Button>
          )}
          <Button variant="secondary" onClick={() => setOpen(!open)}>
            已跟进
          </Button>
          {f.overdue && (
            <Select
              className="w-32"
              value=""
              disabled={snooze.isPending}
              onChange={(e) => e.target.value && snooze.mutate(Number(e.target.value))}
            >
              <option value="">稍后提醒…</option>
              <option value="1">明天</option>
              <option value="3">3 天后</option>
              <option value="7">7 天后</option>
            </Select>
          )}
        </span>
      </div>
      {open && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Input
            className="min-w-64 flex-1"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="怎么跟进的，例如：再次私信提醒寄样"
          />
          <Select className="w-40" value={next} onChange={(e) => setNext(e.target.value)}>
            <option value="">按规则提醒</option>
            <option value="1">1 天后再提醒</option>
            <option value="3">3 天后再提醒</option>
            <option value="7">7 天后再提醒</option>
            <option value="14">14 天后再提醒</option>
          </Select>
          <Button disabled={record.isPending} onClick={() => record.mutate()}>
            记录
          </Button>
        </div>
      )}
      {err && <ErrorText>{errorMessage(err)}</ErrorText>}
    </div>
  );
}
