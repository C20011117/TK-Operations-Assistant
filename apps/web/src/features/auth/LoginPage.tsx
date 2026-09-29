import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useNavigate } from "react-router";
import { z } from "zod";

import { Button, ErrorText, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api/client";

import { meQueryKey, useMe } from "./useMe";

const schema = z.object({
  email: z.string().email("请输入有效的邮箱"),
  password: z.string().min(1, "请输入密码"),
});
type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const me = useMe();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  if (me.data) return <Navigate to="/" replace />;

  const onSubmit = form.handleSubmit(async (values) => {
    setServerError(null);
    const { data, error } = await api.POST("/api/v1/auth/login", { body: values });
    if (error || !data) {
      setServerError(errorMessage(error, "登录失败"));
      return;
    }
    // 切换账号时清掉上一个账号的全部缓存
    qc.clear();
    qc.setQueryData(meQueryKey, data);
    navigate("/", { replace: true });
  });

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-xl font-semibold">TK 多站点达人工作台</h1>
        <p className="mb-6 text-sm text-slate-500">欧洲站点 · 找人、寄样、拍摄、维护一条线完成</p>
        <form onSubmit={onSubmit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm" noValidate>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">邮箱</span>
            <Input type="email" autoComplete="username" {...form.register("email")} />
            {form.formState.errors.email && <ErrorText>{form.formState.errors.email.message}</ErrorText>}
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">密码</span>
            <Input type="password" autoComplete="current-password" {...form.register("password")} />
            {form.formState.errors.password && <ErrorText>{form.formState.errors.password.message}</ErrorText>}
          </label>
          {serverError && <ErrorText>{serverError}</ErrorText>}
          <Button type="submit" className="w-full" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "登录中…" : "登录"}
          </Button>
        </form>
        {import.meta.env.DEV && (
          <p className="mt-4 text-xs text-slate-500">
            开发账号：admin@demo.local、bd.a@demo.local、bd.b@demo.local、bd.c@other.local（密码见 .env 的 SEED_DEV_PASSWORD）
          </p>
        )}
      </div>
    </div>
  );
}
