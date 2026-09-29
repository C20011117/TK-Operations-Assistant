import { createBrowserRouter } from "react-router";

import { MarketsPage } from "@/features/markets/MarketsPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { SystemPage } from "@/features/system/SystemPage";
import { WorkbenchPage } from "@/features/workbench/WorkbenchPage";

import { AppLayout } from "./AppLayout";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <WorkbenchPage /> },
      { path: "markets", element: <MarketsPage /> },
      { path: "system", element: <SystemPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
