import { Navigate, Route, Routes, useParams } from "react-router-dom";

import { HistoryPage } from "@/features/history/HistoryPage";
import { NewBidPage } from "@/features/new-bid/NewBidPage";
import { NotFoundPage } from "@/features/not-found/NotFoundPage";
import { RunPage } from "@/features/run/RunPage";

import { AppShell } from "./AppShell";

/**
 * Routes (SPEC §11.1). `/runs/:runId` opens the live run workspace;
 * the fixture-backed `useRunStream` drives the reducer for demo
 * runIds, and the Bid tab lights up once `bid.ready` arrives.
 *
 * RunPage is rendered with `key={runId}` so navigating between
 * runs (sidebar, history row click) fully remounts it. Without
 * the key, the useReducer state (and the fixture-replay effect)
 * would be reused across runIds — the reducer's id-guard would
 * silently drop the new run's events (they all have ids ≤ the
 * previous run's `lastEventId`), and the page would render the
 * old run's state against the new URL.
 */
function KeyedRunPage() {
  const { runId = "" } = useParams();
  return <RunPage key={runId} />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/new" replace />} />
        <Route path="new" element={<NewBidPage />} />
        <Route path="runs" element={<HistoryPage />} />
        {/* Legacy alias — some early sketches used /history; keep it
            working as a permanent redirect to the canonical /runs. */}
        <Route path="history" element={<Navigate to="/runs" replace />} />
        <Route path="runs/:runId" element={<KeyedRunPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
