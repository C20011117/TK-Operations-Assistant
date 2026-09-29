import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Badge, Button, ErrorText, Select } from "@/components/ui";
import { api, errorMessage, unwrap } from "@/lib/api/client";
import type { DecisionView, RecommendationCard } from "@/lib/api/types";
import { decisionLabels, decisionReasonLabels } from "@/lib/labels";

type Decision = keyof typeof decisionLabels;
type Reason = keyof typeof decisionReasonLabels;

const tone = { keep: "green", needs_verification: "blue", exclude: "red", reconsider: "slate" } as const;
const keepReasons: Reason[] = ["good_fit", "category_match", "competitor_seller", "other"];
const excludeReasons: Reason[] = ["off_target", "audience_mismatch", "too_small", "too_expensive", "data_doubt", "not_eligible", "other"];

export function DecisionBadge({ d }: { d: DecisionView | undefined }) {
  if (!d) return null;
  return <Badge tone={tone[d.decision]}>{decisionLabels[d.decision]}</Badge>;
}

/** 本人对候选的决定。“保留”只表示值得推进，不会自动联系、寄样。 */
export function DecisionBar({
  cmId,
  runId,
  card,
  decision,
}: {
  cmId: string;
  runId: string;
  card: RecommendationCard;
  decision: DecisionView | undefined;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [reason, setReason] = useState<Reason | "">("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["decisions", cmId] });
  const decide = useMutation({
    mutationFn: (d: Decision) =>
      unwrap(
        api.POST("/api/v1/campaign-markets/{cm_id}/creator-decisions", {
          params: { path: { cm_id: cmId } },
          body: {
            creator_id: card.creator.id,
            decision: d,
            reason_code: reason || null,
            note: "",
            run_id: runId,
            evaluation_id: card.evaluation_id,
          },
        }),
      ),
    onSuccess: () => {
      setReason("");
      refresh();
    },
  });
  const collab = useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/campaign-markets/{cm_id}/collaborations", {
          params: { path: { cm_id: cmId } },
          body: { creator_id: card.creator.id, run_id: runId, evaluation_id: card.evaluation_id, note: "" },
        }),
      ),
    onSuccess: (d) => {
      refresh();
      qc.invalidateQueries({ queryKey: ["collaborations"] });
      nav(`/collaborations/${d.id}`);
    },
  });
  const current = decision?.decision;
  const reasons = current === "keep" ? excludeReasons : [...keepReasons, ...excludeReasons];
  const err = decide.error ?? collab.error;

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
      <span className="text-xs text-slate-500">我的决定：</span>
      {current ? <DecisionBadge d={decision} /> : <span className="text-xs text-slate-400">未决定</span>}
      {decision?.reason_code && (
        <span className="text-xs text-slate-500">{decisionReasonLabels[decision.reason_code as Reason] ?? decision.reason_code}</span>
      )}
      <Select className="ml-2 w-44" value={reason} onChange={(e) => setReason(e.target.value as Reason | "")}>
        <option value="">原因（可选）</option>
        {reasons.map((r) => (
          <option key={r} value={r}>
            {decisionReasonLabels[r]}
          </option>
        ))}
      </Select>
      {(["keep", "needs_verification", "exclude"] as Decision[])
        .filter((d) => d !== current)
        .map((d) => (
          <Button key={d} variant="secondary" disabled={decide.isPending} onClick={() => decide.mutate(d)}>
            {decisionLabels[d]}
          </Button>
        ))}
      <span className="ml-auto">
        {decision?.collaboration_id ? (
          <Link className="text-sm text-sky-700 hover:underline" to={`/collaborations/${decision.collaboration_id}`}>
            查看合作 →
          </Link>
        ) : (
          current === "keep" && (
            <Button disabled={collab.isPending} onClick={() => collab.mutate()}>
              准备合作
            </Button>
          )
        )}
      </span>
      {err && (
        <div className="w-full">
          <ErrorText>{errorMessage(err)}</ErrorText>
        </div>
      )}
    </div>
  );
}
