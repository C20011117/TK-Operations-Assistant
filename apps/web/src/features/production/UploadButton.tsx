import { useRef, useState } from "react";

import { Button } from "@/components/ui";
import type { RoundView, VideoVersionView } from "@/lib/api/types";

import { fmtSize } from "./labels";
import { uploadVideo } from "./upload";

/** 选择本机视频文件上传为本轮的下一个版本（V1、V2…）。 */
export function UploadButton({
  round,
  onDone,
  variant = "primary",
}: {
  round: RoundView;
  onDone: (v: VideoVersionView) => void;
  variant?: "primary" | "secondary";
}) {
  const input = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const next = `V${(round.videos[0]?.version_no ?? 0) + 1}`;

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setMsg(null);
    setProgress(0);
    try {
      const { video, replayed } = await uploadVideo(round.id, file, "", setProgress);
      setMsg({
        ok: true,
        text: replayed ? `这个文件已经上传过（${video.label}），没有重复保存` : `已上传 ${video.label}（${fmtSize(video.size_bytes)}）`,
      });
      onDone(video);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "上传失败" });
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <input
        ref={input}
        type="file"
        accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm,.m4v"
        className="hidden"
        data-testid={`upload-${round.id}`}
        onChange={(e) => pick(e.target.files?.[0])}
      />
      <Button variant={variant} disabled={progress !== null} onClick={() => input.current?.click()}>
        {progress !== null ? `上传中 ${Math.round(progress * 100)}%` : `上传视频（${next}）`}
      </Button>
      {msg && <span className={`text-xs ${msg.ok ? "text-emerald-700" : "text-red-600"}`}>{msg.text}</span>}
    </span>
  );
}
