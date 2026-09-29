/** 界面显示用的中文标签与格式化。 */

export const goalLabels = { sales: "带货转化", content: "内容素材", awareness: "曝光种草" } as const;
export const collabLabels = {
  free_sample: "免费寄样换视频",
  paid: "付费合作",
  commission: "纯佣金",
  hybrid: "佣金 + 坑位费",
} as const;
export const samplePolicyLabels = { free: "免费寄样", paid: "付费样品", none: "不寄样", unknown: "未知" } as const;
export const versionStatusLabels = { draft: "草稿", confirmed: "当前版本", superseded: "历史版本" } as const;
export const operatorLabels = { between: "介于", gte: "至少", lte: "至多", eq: "等于", in: "属于" } as const;

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

/** 某个时区的当前当地时间，例如 “10-01 14:05（Europe/London）”。 */
export function localNow(timeZone: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());
}

export function money(amount: string | null | undefined, currency: string): string {
  if (amount == null) return "未知";
  return `${amount} ${currency}`;
}

/** 多行文本 ⇄ 列表（每行一项）。 */
export const toLines = (items: string[]) => items.join("\n");
export const fromLines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
