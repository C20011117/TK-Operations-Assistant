import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { BackendGate } from "./app/BackendGate";
import { router } from "./app/router";
import "./styles/index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BackendGate>
        <RouterProvider router={router} />
      </BackendGate>
    </QueryClientProvider>
  </StrictMode>,
);
