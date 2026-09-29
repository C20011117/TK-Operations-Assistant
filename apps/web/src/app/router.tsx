import { createBrowserRouter } from "react-router";

import { LoginPage } from "@/features/auth/LoginPage";
import { MarketsPage } from "@/features/markets/MarketsPage";
import { SystemPage } from "@/features/system/SystemPage";
import { WorkbenchPage } from "@/features/workbench/WorkbenchPage";

import { AppLayout } from "./AppLayout";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <WorkbenchPage /> },
      { path: "markets", element: <MarketsPage /> },
      { path: "system", element: <SystemPage /> },
    ],
  },
]);
