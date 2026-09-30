import { createBrowserRouter } from "react-router";

import { CampaignDetailPage } from "@/features/campaigns/CampaignDetailPage";
import { CampaignNewPage } from "@/features/campaigns/CampaignNewPage";
import { CampaignsPage } from "@/features/campaigns/CampaignsPage";
import { CollaborationDetailPage } from "@/features/collaborations/CollaborationDetailPage";
import { CollaborationsPage } from "@/features/collaborations/CollaborationsPage";
import { MarketsPage } from "@/features/markets/MarketsPage";
import { ProductDetailPage } from "@/features/products/ProductDetailPage";
import { ProductsPage } from "@/features/products/ProductsPage";
import { RoundPage } from "@/features/production/RoundPage";
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
      { path: "products", element: <ProductsPage /> },
      { path: "products/:productId", element: <ProductDetailPage /> },
      { path: "campaigns", element: <CampaignsPage /> },
      { path: "campaigns/new", element: <CampaignNewPage /> },
      { path: "campaigns/:campaignId", element: <CampaignDetailPage /> },
      { path: "collaborations", element: <CollaborationsPage /> },
      { path: "collaborations/:collabId", element: <CollaborationDetailPage /> },
      { path: "rounds/:roundId", element: <RoundPage /> },
      { path: "markets", element: <MarketsPage /> },
      { path: "system", element: <SystemPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);
