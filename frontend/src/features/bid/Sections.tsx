/**
 * Flat, read-only sections rendered below the LinesTable:
 *   - ExclusionsSection · LaborSection · UnreadableSection
 *   - ConflictsSection · AssumptionsSection · ContractorQuestionsSection
 *
 * SPEC §11.2 calls these out as distinct blocks; keeping them in
 * one file prevents accidental prop-drill differences. Each
 * section is a Card with a header + list; empty sections return
 * null so the workspace stays uncluttered.
 */
import {
  AlertTriangleIcon,
  FileTextIcon,
  HelpCircleIcon,
  MessageCircleQuestionIcon,
  ScissorsIcon,
  SquareDashedIcon,
} from "lucide-react";

import type {
  AssumptionView,
  Bid,
  ContractorQuestionView,
  ExclusionLine,
  LaborLine,
  UnreadableLine,
} from "@/api";
import { CitationLink } from "@/components/CitationLink";

type Props = {
  bid: Bid;
  pdfUrl: string | null;
};

export function BidSections({ bid, pdfUrl }: Props) {
  return (
    <div className="space-y-5">
      <ExclusionsSection items={bid.exclusions} pdfUrl={pdfUrl} />
      <LaborSection items={bid.labor} pdfUrl={pdfUrl} />
      <UnreadableSection items={bid.unreadable} />
      <ConflictsSection bid={bid} pdfUrl={pdfUrl} />
      <AssumptionsSection items={bid.assumptions} />
      <ContractorQuestionsSection items={bid.contractor_questions} />
    </div>
  );
}

function SectionCard({
  title,
  icon: Icon,
  tone = "neutral",
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  tone?: "neutral" | "warn";
  children: React.ReactNode;
}) {
  const toneClass =
    tone === "warn"
      ? "border-warn-amber/30 bg-warn-amber/5"
      : "border-border bg-card";
  return (
    <section
      aria-label={title}
      className={`rounded-md border ${toneClass}`}
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
        {title}
      </header>
      <div className="px-4 py-3 text-sm">{children}</div>
    </section>
  );
}

function ExclusionsSection({
  items,
  pdfUrl,
}: {
  items: ExclusionLine[];
  pdfUrl: string | null;
}) {
  if (items.length === 0) return null;
  return (
    <SectionCard title="Exclusions" icon={ScissorsIcon}>
      <ul className="space-y-1.5">
        {items.map((e, i) => (
          <li key={i} className="flex items-start gap-2">
            <CitationLink source={e.source} pdfUrl={pdfUrl} />
            <span className="text-foreground">{e.description}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function LaborSection({ items, pdfUrl }: { items: LaborLine[]; pdfUrl: string | null }) {
  if (items.length === 0) return null;
  return (
    <SectionCard title="Labor — not priced" icon={FileTextIcon}>
      <ul className="space-y-1.5">
        {items.map((l, i) => (
          <li key={i} className="flex items-start gap-2">
            {l.source ? <CitationLink source={l.source} pdfUrl={pdfUrl} /> : null}
            <span className="min-w-0 flex-1">
              <span className="text-foreground">{l.description}</span>
              {l.quantity_text ? (
                <span className="ml-2 tabular text-xs text-muted-foreground">
                  {l.quantity_text}
                  {l.unit_raw ? ` ${l.unit_raw}` : ""}
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted-foreground">
        Labor lines are quoted by the estimator; the agent only lists
        them for reference.
      </p>
    </SectionCard>
  );
}

function UnreadableSection({ items }: { items: UnreadableLine[] }) {
  if (items.length === 0) return null;
  return (
    <SectionCard title="Unreadable" icon={SquareDashedIcon}>
      <ul className="space-y-1.5">
        {items.map((u, i) => (
          <li key={i} className="text-muted-foreground">
            {u.description}
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function ConflictsSection({ bid, pdfUrl }: { bid: Bid; pdfUrl: string | null }) {
  if (bid.conflicts.length === 0) return null;
  return (
    <SectionCard title="Conflicts" icon={AlertTriangleIcon} tone="warn">
      <ul className="space-y-3">
        {bid.conflicts.map((c) => (
          <li key={c.id}>
            <p className="font-medium text-foreground">
              {c.explanation || c.type.replace(/_/g, " ")}
            </p>
            <div className="mt-1 grid grid-cols-2 gap-3">
              <ConflictSideCard label="Side A" source={c.side_a.source} pdfUrl={pdfUrl} />
              <ConflictSideCard label="Side B" source={c.side_b.source} pdfUrl={pdfUrl} />
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function ConflictSideCard({
  label,
  source,
  pdfUrl,
}: {
  label: string;
  source: import("@/api").SourceRef;
  pdfUrl: string | null;
}) {
  return (
    <div className="rounded border border-border bg-background px-2 py-1 text-xs">
      <div className="flex items-center gap-2 text-muted-foreground">
        <span className="font-medium uppercase tracking-wide">{label}</span>
        <CitationLink source={source} pdfUrl={pdfUrl} />
      </div>
      {source.quote ? <p className="mt-0.5 text-foreground">{source.quote}</p> : null}
    </div>
  );
}

function AssumptionsSection({ items }: { items: AssumptionView[] }) {
  if (items.length === 0) return null;
  return (
    <SectionCard title="Assumptions" icon={HelpCircleIcon}>
      <ul className="space-y-1.5">
        {items.map((a, i) => (
          <li key={i}>
            <span className="font-mono text-xs text-muted-foreground">{a.item_id}</span>
            <span className="ml-2 text-foreground">{a.text}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function ContractorQuestionsSection({
  items,
}: {
  items: ContractorQuestionView[];
}) {
  if (items.length === 0) return null;
  return (
    <SectionCard title="Questions for the contractor" icon={MessageCircleQuestionIcon} tone="warn">
      <ul className="space-y-1.5">
        {items.map((q, i) => (
          <li key={i}>
            <span className="text-foreground">{q.text}</span>
            <span className="ml-2 text-xs text-muted-foreground">
              · {q.basis.kind.replace("_", " ")} ({q.basis.ref_id})
            </span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
