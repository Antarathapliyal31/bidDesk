import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import {
  useCreateRun,
  type CreateRunPayload,
  type FaultKey,
  type Sample,
} from "@/api";
import { hasModKey } from "@/app/platform";
import { useDocumentTitle } from "@/app/useDocumentTitle";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { messageForError } from "@/lib/apiMessages";

import { BranchSelect } from "./BranchSelect";
import { DemoFaultControls } from "./DemoFaultControls";
import { PasteListEditor } from "./PasteListEditor";
import { PdfDropzone } from "./PdfDropzone";
import { SampleInputs } from "./SampleInputs";
import { SubmitBar } from "./SubmitBar";
import { validatePastedText, validatePdfFile } from "./validation";

/**
 * New-bid form (SPEC §11.1, §11.2). Owns every piece of form state
 * and wires the page-level concerns:
 *   - ⌘/Ctrl+Enter submits from anywhere on the page (incl. the
 *     textarea); suppressed when a modal dialog is open so the
 *     command menu / shortcuts sheet can trap focus.
 *   - Window-level dragover/drop are prevent-defaulted so a missed
 *     drop never opens the PDF in the browser tab.
 *   - All §11.4 states: empty (submit disabled with reason), invalid
 *     (inline errors from children), loading (SubmitBar "Starting…"),
 *     submit-error (toast + inline + friendly message per ApiError
 *     code), success (navigate to run page).
 */
type InputMode = "pdf" | "paste";

const DEFAULT_BRANCH_ID = "BR-NJ-01";

export function NewBidPage() {
  useDocumentTitle("New bid");
  const navigate = useNavigate();
  const createRun = useCreateRun();

  const [mode, setMode] = useState<InputMode>("pdf");
  const [file, setFileState] = useState<File | null>(null);
  const [text, setTextState] = useState("");
  const [branchId, setBranchIdState] = useState<string | null>(null);
  const [faults, setFaultsState] = useState<Set<FaultKey>>(new Set());
  const [submitError, setSubmitError] = useState<string | undefined>(undefined);

  const submitRef = useRef<HTMLButtonElement>(null);

  // ─── Setters that clear any previous submit error ───────────────
  const setFile = (next: File | null) => {
    setSubmitError(undefined);
    setFileState(next);
  };
  const setText = (next: string) => {
    setSubmitError(undefined);
    setTextState(next);
  };
  const setBranchId = (next: string) => {
    setSubmitError(undefined);
    setBranchIdState(next);
  };
  const setFaults: React.Dispatch<React.SetStateAction<Set<FaultKey>>> = (next) => {
    setSubmitError(undefined);
    setFaultsState(next);
  };

  // ─── Disabled-reason reasoning ──────────────────────────────────
  const disabledReason: string | undefined =
    branchId === null
      ? "Choose a branch to continue."
      : mode === "pdf" && !file
        ? "Drop or choose a PDF to continue."
        : mode === "paste" && validatePastedText(text)?.kind === "empty"
          ? "Paste a materials list to continue."
          : mode === "paste" && validatePastedText(text)?.kind === "too_long"
            ? "Shorten the paste to continue."
            : mode === "pdf" && file && validatePdfFile(file)
              ? "Fix the file to continue."
              : undefined;
  const disabled = Boolean(disabledReason);

  // ─── Build + submit the payload ─────────────────────────────────
  const submit = useCallback(async () => {
    if (disabled || createRun.isPending) return;
    if (branchId === null) return;
    setSubmitError(undefined);
    const faultsList = Array.from(faults);
    const payload: CreateRunPayload =
      mode === "pdf" && file
        ? { file, branch_id: branchId, faults: faultsList }
        : { text, branch_id: branchId, faults: faultsList };
    try {
      const { run_id } = await createRun.mutateAsync(payload);
      navigate(`/runs/${run_id}`);
    } catch (e) {
      const msg = messageForError(e);
      setSubmitError(msg);
      toast.error("Couldn't start the run.", { description: msg });
    }
  }, [disabled, createRun, branchId, faults, mode, file, text, navigate]);

  // ─── Keyboard: ⌘/Ctrl+Enter from anywhere, except over a modal ──
  useEffect(() => {
    function modalOpen(): boolean {
      return document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Enter") return;
      if (!hasModKey(e)) return;
      if (e.repeat || e.isComposing || e.defaultPrevented) return;
      if (modalOpen()) return;
      e.preventDefault();
      void submit();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [submit]);

  // ─── Window drag guard: don't let missed drops open the PDF ─────
  useEffect(() => {
    function block(e: DragEvent) {
      e.preventDefault();
    }
    window.addEventListener("dragover", block);
    window.addEventListener("drop", block);
    return () => {
      window.removeEventListener("dragover", block);
      window.removeEventListener("drop", block);
    };
  }, []);

  // ─── Sample pick: prefill the form, default branch, focus Submit ─
  async function onPickSample(s: Sample) {
    if (branchId === null) setBranchId(DEFAULT_BRANCH_ID);
    if (s.kind === "pdf") {
      setMode("pdf");
      try {
        const res = await fetch(s.file_url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const f = new File([blob], s.name, { type: "application/pdf" });
        setFile(f);
      } catch {
        toast.error("Couldn't load the sample PDF.", {
          description:
            "The backend's make samples hasn't populated frontend/public/samples yet.",
        });
      }
    } else {
      setMode("paste");
      setText(s.text);
    }
    // Focus runs after the sample-driven state updates commit.
    queueMicrotask(() => submitRef.current?.focus());
  }

  function onToggleFault(key: FaultKey, checked: boolean) {
    setFaults((prev) => {
      const next = new Set(prev);
      if (checked) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">New bid</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Upload an RFP or paste a material list to get started.
        </p>
      </header>

      <Tabs value={mode} onValueChange={(v) => setMode(v as InputMode)}>
        <TabsList>
          <TabsTrigger value="pdf">Upload PDF</TabsTrigger>
          <TabsTrigger value="paste">Paste list</TabsTrigger>
        </TabsList>
        <TabsContent value="pdf" className="mt-4">
          <PdfDropzone file={file} onChange={setFile} />
        </TabsContent>
        <TabsContent value="paste" className="mt-4">
          <PasteListEditor value={text} onChange={setText} />
        </TabsContent>
      </Tabs>

      <BranchSelect value={branchId} onChange={setBranchId} />

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Start from a sample
        </h2>
        <SampleInputs onPick={onPickSample} submitRef={submitRef} />
      </section>

      <DemoFaultControls selected={faults} onToggle={onToggleFault} />

      <SubmitBar
        ref={submitRef}
        disabled={disabled}
        disabledReason={disabledReason}
        faultCount={faults.size}
        submitting={createRun.isPending}
        errorMessage={submitError}
        onSubmit={() => void submit()}
      />
    </div>
  );
}
