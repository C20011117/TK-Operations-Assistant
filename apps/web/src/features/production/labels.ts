export const roundStatusLabels = {
  briefing: "准备拍摄包",
  awaiting_video: "等视频",
  in_review: "待审核",
  revision_requested: "等返修",
  accepted: "已验收",
  cancelled: "已取消",
} as const;

export const roundTone = {
  briefing: "slate",
  awaiting_video: "blue",
  in_review: "amber",
  revision_requested: "amber",
  accepted: "green",
  cancelled: "slate",
} as const;

export const videoStatusLabels = {
  in_review: "待审核",
  changes_requested: "要求返修",
  accepted: "验收通过",
} as const;

export const categoryLabels = {
  script: "脚本 / 口播",
  visual: "画面",
  audio: "声音 / 音乐",
  product: "产品展示",
  compliance: "合规",
  other: "其他",
} as const;

export const LANG_LABELS: Record<string, string> = {
  en: "英语",
  de: "德语",
  fr: "法语",
  it: "意大利语",
  es: "西班牙语",
  nl: "荷兰语",
  pl: "波兰语",
  pt: "葡萄牙语",
  ga: "爱尔兰语",
};
export const langName = (code: string) => LANG_LABELS[code] ?? code;

/** 毫秒 → mm:ss.d */
export function fmtTc(ms: number): string {
  const total = Math.max(0, Math.round(ms / 100));
  const d = total % 10;
  const s = Math.floor(total / 10) % 60;
  const m = Math.floor(total / 600);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${d}`;
}

/** "1:02.5" / "62.5" / "01:02" → 毫秒；格式不对返回 null */
export function parseTc(v: string): number | null {
  const t = v.trim();
  const m = /^(?:(\d{1,3}):)?(\d{1,2}(?:\.\d{1,3})?)$/.exec(t) ?? /^(\d+(?:\.\d{1,3})?)$/.exec(t);
  if (!m) return null;
  if (m.length === 3) {
    const min = m[1] ? Number(m[1]) : 0;
    const sec = Number(m[2]);
    if (m[1] && sec >= 60) return null;
    return Math.round((min * 60 + sec) * 1000);
  }
  return Math.round(Number(m[1]) * 1000);
}

export function fmtSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}
