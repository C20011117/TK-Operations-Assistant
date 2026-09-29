import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

export const meQueryKey = ["auth", "me"] as const;

export function useMe() {
  return useQuery({
    queryKey: meQueryKey,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      if (response.status === 401) return null;
      if (!data) throw new Error("无法获取当前用户");
      return data;
    },
    staleTime: 60_000,
    retry: false,
  });
}
