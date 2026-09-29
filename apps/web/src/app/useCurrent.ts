import { useOutletContext } from "react-router";

import type { Me } from "@/lib/api/types";

export function useCurrent() {
  const { me } = useOutletContext<{ me: Me }>();
  if (!me.current) throw new Error("no current tenant");
  return { me, current: me.current };
}
