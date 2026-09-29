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

export const runStatusLabels = {
  queued: "排队中",
  running: "进行中",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "失败",
  cancelled: "已取消",
} as const;
export const stageLabels: Record<string, string> = {
  load_context: "准备",
  discover: "FastMoss 搜索",
  hard_filter: "条件判断",
  assess: "AI 判断",
  done: "完成",
};
export const groupLabels = { qualified: "合格", needs_verification: "待核实", excluded: "已排除" } as const;
export const fitLabels = { high: "适配度高", medium: "适配度中", low: "适配度低", unknown: "适配度未知" } as const;
export const callStatusLabels: Record<string, string> = {
  pending: "进行中",
  succeeded: "成功",
  empty: "无结果（不扣费）",
  failed: "失败",
  insufficient_credits: "额度不足",
  rate_limited: "被限流",
  unauthorized: "Key 无效",
  unknown: "结果未知",
};

/** 大数字缩写：12345 → 1.2 万 */
export function compact(v: string | null | undefined): string {
  if (v == null) return "未知";
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)} 万`;
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}
