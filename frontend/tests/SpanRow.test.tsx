/**
 * SpanRow unit tests: disclosure behavior, state-dot semantics, and
 * the four detail-slot renderings (ToolCall · Retry · Validation ·
 * InjectedBadge). Driven with fabricated SpanView values rather
 * than the fixture — the fixture only exercises tool calls.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { SpanView } from "@/lib/runReducer";
import type { SpanTree } from "@/lib/trace";

import { SpanRow } from "../src/features/run/SpanRow";

function fx(overrides: Partial<SpanView> = {}): SpanView {
  return {
    id: "s1",
    parentId: null,
    agent: "orchestrator",
    name: "orchestrator.loop",
    label: "Triage + enrich + price",
    state: "finished",
    startedAtTs: "2026-11-11T00:00:00Z",
    durationMs: 1234,
    summary: null,
    errorKind: null,
    errorMessage: null,
    willRetry: false,
    tool: null,
    retry: null,
    validation: null,
    injected: false,
    ...overrides,
  };
}

const EMPTY_TREE: SpanTree = { rootIds: [], childrenById: {} };

function Harness({ span, tree, spans }: { span: SpanView; tree?: SpanTree; spans?: Record<string, SpanView> }) {
  return (
    <ul>
      <SpanRow
        span={span}
        tree={tree ?? EMPTY_TREE}
        spans={spans ?? { [span.id]: span }}
        depth={0}
      />
    </ul>
  );
}

describe("SpanRow — header", () => {
  it("renders the span name, label, duration (ms), and a dot labeled by state", () => {
    render(<Harness span={fx({ durationMs: 850, state: "finished" })} />);
    expect(screen.getByText("orchestrator.loop")).toBeInTheDocument();
    expect(screen.getByText("Triage + enrich + price")).toBeInTheDocument();
    expect(screen.getByText("850ms")).toBeInTheDocument();
    expect(screen.getByLabelText("finished")).toBeInTheDocument();
  });

  it("formats sub-second durations with ms, >=1s with one-decimal seconds", () => {
    render(<Harness span={fx({ durationMs: 12_345 })} />);
    expect(screen.getByText("12.3s")).toBeInTheDocument();
  });

  it("shows no duration badge while the span is running", () => {
    render(<Harness span={fx({ state: "running", durationMs: null })} />);
    expect(screen.queryByText(/ms$|s$/)).toBeNull();
    expect(screen.getByLabelText("running")).toBeInTheDocument();
  });

  it("renders an InjectedBadge inline when span.injected is true", () => {
    render(<Harness span={fx({ injected: true })} />);
    expect(screen.getByText(/demo fault/i)).toBeInTheDocument();
  });
});

describe("SpanRow — disclosure", () => {
  it("finished spans start collapsed; Space toggles aria-expanded", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        span={fx({
          state: "finished",
          tool: { name: "classify_items", argsPreview: "26 items", ok: true, resultPreview: "price: 26" },
        })}
      />,
    );
    const toggle = screen.getByRole("button", { name: /orchestrator\.loop/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("classify_items")).toBeNull();
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("classify_items")).toBeInTheDocument();
  });

  it("running spans auto-expand (surface the live work)", () => {
    render(
      <Harness
        span={fx({ state: "running", durationMs: null, tool: { name: "work", argsPreview: "..." } })}
      />,
    );
    const toggle = screen.getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("work")).toBeInTheDocument();
  });

  it("failed spans auto-expand (surface the error)", () => {
    render(
      <Harness
        span={fx({
          state: "failed",
          errorKind: "validation",
          errorMessage: "pricing.sku missing",
        })}
      />,
    );
    const toggle = screen.getByRole("button");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/pricing\.sku missing/)).toBeInTheDocument();
    expect(screen.getByText(/validation/)).toBeInTheDocument();
  });

  it("spans with no details at all leave the chevron hidden and aren't toggleable", async () => {
    const user = userEvent.setup();
    render(<Harness span={fx({ summary: null })} />);
    const toggle = screen.getByRole("button");
    await user.click(toggle);
    // No panel ever appears.
    expect(screen.queryByRole("region")).toBeNull();
  });
});

describe("SpanRow — detail slots", () => {
  it("ToolCallDetail renders tool name + args/result + ok chip", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        span={fx({
          tool: { name: "price_item", argsPreview: "SKU=A123", ok: true, resultPreview: "$12.50" },
        })}
      />,
    );
    await user.click(screen.getByRole("button"));
    expect(screen.getByText("price_item")).toBeInTheDocument();
    expect(screen.getByText(/SKU=A123/)).toBeInTheDocument();
    expect(screen.getByText(/\$12\.50/)).toBeInTheDocument();
    expect(screen.getByText(/^ok$/)).toBeInTheDocument();
  });

  it("RetryNote renders the reason + attempt + delay", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        span={fx({
          state: "running",
          retry: { attempt: 1, delay_s: 2, reason: "Pricing agent timed out (45s)" },
        })}
      />,
    );
    // Running = auto-expanded.
    const region = screen.getByRole("region");
    expect(within(region).getByText(/timed out/i)).toBeInTheDocument();
    expect(within(region).getByText(/retrying 1\/1/i)).toBeInTheDocument();
    expect(within(region).getByText(/backoff: 2\.0s/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button"));
  });

  it("ValidationErrorDetail lists the errors and names the boundary", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        span={fx({
          state: "running",
          validation: {
            boundary: "pricing_agent→orchestrator",
            errors: ["unit_price_cents missing", "confidence out of range"],
            will_retry: true,
          },
        })}
      />,
    );
    expect(screen.getByText(/pricing_agent→orchestrator/)).toBeInTheDocument();
    expect(screen.getByText(/unit_price_cents missing/)).toBeInTheDocument();
    expect(screen.getByText(/confidence out of range/)).toBeInTheDocument();
    await user.click(screen.getByRole("button"));
  });
});

describe("SpanRow — nesting", () => {
  it("renders child spans as nested rows when expanded", async () => {
    const user = userEvent.setup();
    const parent = fx({ id: "p", state: "finished" });
    const child = fx({ id: "c", parentId: "p", name: "pricing.batch", label: null });
    const spans = { p: parent, c: child };
    const tree: SpanTree = { rootIds: ["p"], childrenById: { p: ["c"] } };
    render(<Harness span={parent} tree={tree} spans={spans} />);
    // Collapsed by default (finished) — child not in DOM.
    expect(screen.queryByText("pricing.batch")).toBeNull();
    await user.click(screen.getByRole("button", { name: /orchestrator\.loop/i }));
    expect(screen.getByText("pricing.batch")).toBeInTheDocument();
  });
});
