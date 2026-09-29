import { useQuery } from "@tanstack/react-query";

import { Badge, Button, Input, Select } from "@/components/ui";
import { api, unwrap } from "@/lib/api/client";
import type { Criterion, FieldSpec } from "@/lib/api/types";
import { operatorLabels } from "@/lib/labels";

export type Row = Omit<Criterion, "expected" | "criterion_id"> & { criterion_id: string; expected: unknown };

const supportTone = { search_filter: "green", result_field: "blue", not_provided: "amber" } as const;
const supportShort = { search_filter: "搜索时筛选", result_field: "按结果判断", not_provided: "需人工核实" } as const;

export function useFieldSpecs() {
  return useQuery({
    queryKey: ["criteria-fields"],
    queryFn: () => unwrap(api.GET("/api/v1/criteria/fields")),
    staleTime: Infinity,
  });
}

function defaultExpected(spec: FieldSpec, op: string): unknown {
  if (spec.value_type === "int_range" && op === "between") return { min: "", max: "" };
  if (spec.value_type === "bool") return true;
  if (spec.value_type === "enum") return (spec.options ?? [])[0]?.value ?? "";
  if (spec.value_type === "enum_multi") return [];
  return "";
}

export function newRow(spec: FieldSpec): Row {
  const op = spec.operators[0] ?? "eq";
  return {
    criterion_id: crypto.randomUUID(),
    field_key: spec.key,
    operator: op,
    expected: defaultExpected(spec, op),
    hardness: "soft",
    unknown_policy: "keep",
    provenance: "user",
    note: "",
  };
}

function ValueInput({
  spec,
  row,
  currency,
  marketLanguages,
  onChange,
  disabled,
}: {
  spec: FieldSpec;
  row: Row;
  currency: string;
  marketLanguages: string[];
  onChange: (v: unknown) => void;
  disabled: boolean;
}) {
  const unit = spec.value_type === "money" ? currency : spec.unit;
  if (spec.value_type === "int_range" && row.operator === "between") {
    const v = (row.expected ?? {}) as { min?: string | null; max?: string | null };
    return (
      <div className="flex items-center gap-1">
        <Input disabled={disabled} inputMode="numeric" className="w-28" placeholder="下限" value={v.min ?? ""} onChange={(e) => onChange({ ...v, min: e.target.value })} />
        <span className="text-slate-400">–</span>
        <Input disabled={disabled} inputMode="numeric" className="w-28" placeholder="上限" value={v.max ?? ""} onChange={(e) => onChange({ ...v, max: e.target.value })} />
        {unit && <span className="text-xs text-slate-500">{unit}</span>}
      </div>
    );
  }
  if (spec.value_type === "bool") {
    return (
      <Select disabled={disabled} value={row.expected ? "true" : "false"} onChange={(e) => onChange(e.target.value === "true")} className="w-24">
        <option value="true">是</option>
        <option value="false">否</option>
      </Select>
    );
  }
  if (spec.value_type === "enum") {
    return (
      <Select disabled={disabled} value={String(row.expected ?? "")} onChange={(e) => onChange(e.target.value)} className="w-36">
        {(spec.options ?? []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    );
  }
  if (spec.value_type === "enum_multi") {
    const selected = new Set((row.expected as string[]) ?? []);
    const opts = (spec.key === "content_language" ? spec.options?.filter((o) => marketLanguages.includes(o.value)) : spec.options) ?? [];
    return (
      <div className="flex flex-wrap gap-3">
        {opts.map((o) => (
          <label key={o.value} className="flex items-center gap-1 text-sm">
            <input
              type="checkbox"
              disabled={disabled}
              checked={selected.has(o.value)}
              onChange={(e) => {
                const next = new Set(selected);
                if (e.target.checked) next.add(o.value);
                else next.delete(o.value);
                onChange([...next]);
              }}
            />
            {o.label}
          </label>
        ))}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1">
      <Input disabled={disabled} inputMode="decimal" className="w-32" value={String(row.expected ?? "")} onChange={(e) => onChange(e.target.value)} />
      {unit && <span className="text-xs text-slate-500">{unit}</span>}
    </div>
  );
}

/** 条件表格编辑器：字段来自白名单，每行标明 FastMoss 实际能力。 */
export function CriteriaEditor({
  rows,
  onChange,
  currency,
  marketLanguages,
  disabled = false,
}: {
  rows: Row[];
  onChange: (rows: Row[]) => void;
  currency: string;
  marketLanguages: string[];
  disabled?: boolean;
}) {
  const specs = useFieldSpecs();
  if (!specs.data) return <p className="text-sm text-slate-500">加载条件字段…</p>;
  const byKey = new Map(specs.data.map((s) => [s.key, s]));
  const update = (id: string, patch: Partial<Row>) => onChange(rows.map((r) => (r.criterion_id === id ? { ...r, ...patch } : r)));

  return (
    <div className="space-y-3">
      {rows.length === 0 && <p className="text-sm text-slate-500">还没有条件。可以只用关键词搜索，也可以添加条件。</p>}
      {rows.map((r) => {
        const spec = byKey.get(r.field_key);
        if (!spec) return null;
        return (
          <div key={r.criterion_id} className="rounded-md border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Select
                disabled={disabled}
                className="w-44"
                value={r.field_key}
                onChange={(e) => {
                  const s = byKey.get(e.target.value)!;
                  update(r.criterion_id, { field_key: s.key, operator: s.operators[0] ?? "eq", expected: defaultExpected(s, s.operators[0] ?? "eq") });
                }}
              >
                {specs.data.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.label}
                  </option>
                ))}
              </Select>
              <Select
                disabled={disabled || spec.operators.length === 1}
                className="w-24"
                value={r.operator}
                onChange={(e) => {
                  const op = e.target.value as Row["operator"];
                  update(r.criterion_id, { operator: op, expected: defaultExpected(spec, op) });
                }}
              >
                {spec.operators.map((o) => (
                  <option key={o} value={o}>
                    {operatorLabels[o]}
                  </option>
                ))}
              </Select>
              <ValueInput
                spec={spec}
                row={r}
                currency={currency}
                marketLanguages={marketLanguages}
                disabled={disabled}
                onChange={(v) => update(r.criterion_id, { expected: v })}
              />
              <Select
                disabled={disabled}
                className="w-28"
                value={r.hardness}
                onChange={(e) => {
                  const hardness = e.target.value as Row["hardness"];
                  update(r.criterion_id, { hardness, unknown_policy: hardness === "soft" ? "keep" : r.unknown_policy });
                }}
              >
                <option value="hard">硬条件</option>
                <option value="soft">软条件</option>
              </Select>
              {r.hardness === "hard" && (
                <Select
                  disabled={disabled}
                  className="w-40"
                  value={r.unknown_policy}
                  onChange={(e) => update(r.criterion_id, { unknown_policy: e.target.value as Row["unknown_policy"] })}
                >
                  <option value="keep">未知时：待核实</option>
                  <option value="exclude">未知时：排除</option>
                </Select>
              )}
              <span className="ml-auto flex items-center gap-2">
                <span title={spec.support_label}>
                  <Badge tone={supportTone[spec.support]}>{supportShort[spec.support]}</Badge>
                </span>
                {!disabled && (
                  <Button variant="ghost" onClick={() => onChange(rows.filter((x) => x.criterion_id !== r.criterion_id))}>
                    删除
                  </Button>
                )}
              </span>
            </div>
            <p className="mt-1.5 text-xs text-slate-500">
              {spec.description}
              {spec.data_quality_note && <span className="text-amber-700"> · {spec.data_quality_note}</span>}
            </p>
          </div>
        );
      })}
      {!disabled && (
        <Button variant="secondary" onClick={() => onChange([...rows, newRow(specs.data[0]!)])}>
          添加条件
        </Button>
      )}
    </div>
  );
}
