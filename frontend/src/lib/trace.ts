/**
 * Pure helpers for rendering the trace timeline from a flat
 * `spans` map + `rootSpanIds` ordering.
 *
 * The reducer builds the forest on the fly (each span records
 * `parentId`, root ids are ordered as they arrive); the renderer
 * just needs a `childrenById` index. Keeping it in one pure helper
 * keeps the recursive SpanRow component shallow and unit-tests the
 * ordering rule in one place.
 *
 * Child order: by `startedAtTs` ascending with span id as a stable
 * tiebreaker. SSE delivers events in id order and id grows
 * monotonically, so ts ordering ≈ arrival order — but ties can
 * happen when two spans share a `ts` (microseconds truncated), and
 * a stable secondary key keeps the UI from reshuffling on replay.
 */
import type { SpanView } from "./runReducer";

export type SpanTree = {
  rootIds: readonly string[];
  childrenById: Readonly<Record<string, readonly string[]>>;
};

export function buildSpanTree(
  spans: Record<string, SpanView>,
  rootSpanIds: readonly string[],
): SpanTree {
  const childrenById: Record<string, string[]> = {};
  for (const id of Object.keys(spans)) {
    const span = spans[id];
    if (!span) continue;
    const parentId = span.parentId;
    // Skip spans whose parentId points nowhere (e.g. an event stream
    // that references a parent we never recorded) — they'd orphan
    // the row and never render. Dropping is safer than promoting to
    // a synthetic root.
    if (parentId === null) continue;
    if (!spans[parentId]) continue;
    if (!childrenById[parentId]) childrenById[parentId] = [];
    childrenById[parentId].push(id);
  }
  for (const list of Object.values(childrenById)) {
    list.sort((a, b) => {
      const sa = spans[a];
      const sb = spans[b];
      if (!sa || !sb) return 0;
      if (sa.startedAtTs === sb.startedAtTs) return a < b ? -1 : a > b ? 1 : 0;
      return sa.startedAtTs < sb.startedAtTs ? -1 : 1;
    });
  }
  return { rootIds: rootSpanIds, childrenById };
}
