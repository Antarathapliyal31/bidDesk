import { Navigate, Route, Routes } from "react-router-dom";

import { HistoryPage } from "@/features/history/HistoryPage";
import { NewBidPage } from "@/features/new-bid/NewBidPage";
import { NotFoundPage } from "@/features/not-found/NotFoundPage";
import { RunPagePlaceholder } from "@/features/run/RunPagePlaceholder";

import { AppShell } from "./AppShell";

/**
 * Routes (SPEC §11.1). `/runs/:runId` is a Phase-7 placeholder so the
 * new-bid submit path stays verifiable today.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/new" replace />} />
        <Route path="new" element={<NewBidPage />} />
        <Route path="runs" element={<HistoryPage />} />
        <Route path="runs/:runId" element={<RunPagePlaceholder />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
