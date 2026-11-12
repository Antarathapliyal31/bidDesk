// @vitest-environment node
/**
 * `buildSpanTree` is pure and deterministic. Tests cover:
 *   - childrenById indexes by startedAtTs ascending (stable id tiebreak)
 *   - rootIds are returned verbatim (reducer's ordering is the source of truth)
 *   - spans referencing a missing parent are dropped (never promoted to roots)
 *   - cycle-safe: no infinite loops if `parentId` points back up a chain
 */
import { describe, expect, it } from "vitest";

import type { SpanView } from "@/lib/runReducer";
import { buildSpanTree } from "@/lib/trace";

function span(id: string, parentId: string | null, startedAtTs: string): SpanView {
  return {
    id,
    parentId,
    agent: null,
    name: `span.${id}`,
    label: null,
    state: "finished",
    startedAtTs,
    durationMs: 10,
    summary: null,
    errorKind: null,
    errorMessage: null,
    willRetry: false,
    tool: null,
    retry: null,
    validation: null,
    injected: false,
  };
}

function mapOf(...xs: SpanView[]): Record<string, SpanView> {
  return Object.fromEntries(xs.map((s) => [s.id, s]));
}

describe("buildSpanTree", () => {
  it("returns rootIds verbatim and empty children when all spans are roots", () => {
    const spans = mapOf(span("a", null, "t1"), span("b", null, "t2"));
    const tree = buildSpanTree(spans, ["a", "b"]);
    expect(tree.rootIds).toEqual(["a", "b"]);
    expect(tree.childrenById).toEqual({});
  });

  it("indexes children under their parent in startedAt-ascending order", () => {
    const spans = mapOf(
      span("root", null, "t1"),
      span("child_b", "root", "t3"),
      span("child_a", "root", "t2"),
      span("child_c", "root", "t4"),
    );
    const tree = buildSpanTree(spans, ["root"]);
    expect(tree.childrenById.root).toEqual(["child_a", "child_b", "child_c"]);
  });

  it("falls back to id ordering when two children share a startedAtTs", () => {
    const spans = mapOf(
      span("root", null, "t1"),
      span("beta", "root", "t2"),
      span("alpha", "root", "t2"),
    );
    const tree = buildSpanTree(spans, ["root"]);
    // Same ts → ascending id.
    expect(tree.childrenById.root).toEqual(["alpha", "beta"]);
  });

  it("drops spans whose parent id is not in the map (dangling references)", () => {
    const spans = mapOf(span("root", null, "t1"), span("orphan", "ghost", "t2"));
    const tree = buildSpanTree(spans, ["root"]);
    expect(tree.rootIds).toEqual(["root"]); // orphan NOT promoted to a root
    expect(tree.childrenById).toEqual({}); // orphan has no slot either
  });

  it("is cycle-safe: a → b → a still terminates without infinite recursion", () => {
    const spans = mapOf(span("a", "b", "t1"), span("b", "a", "t2"));
    // We just need buildSpanTree to return in bounded time; the tree
    // itself is "wrong" but the renderer short-circuits via depth and
    // React's keyed children.
    const tree = buildSpanTree(spans, []);
    expect(tree.childrenById.a).toEqual(["b"]);
    expect(tree.childrenById.b).toEqual(["a"]);
  });

  it("deeply nested spans index one level at a time", () => {
    const spans = mapOf(
      span("l1", null, "t1"),
      span("l2", "l1", "t2"),
      span("l3", "l2", "t3"),
    );
    const tree = buildSpanTree(spans, ["l1"]);
    expect(tree.childrenById.l1).toEqual(["l2"]);
    expect(tree.childrenById.l2).toEqual(["l3"]);
    expect(tree.childrenById.l3).toBeUndefined();
  });
});
