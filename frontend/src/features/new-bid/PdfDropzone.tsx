import { FileTextIcon, UploadCloudIcon, XIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { InlineError } from "@/components/InlineError";
import { cn } from "@/lib/utils";

import { MAX_PAGES, MAX_UPLOAD_MB } from "./limits";
import { pdfErrorMessage, validatePdfFile, type PdfValidationError } from "./validation";

/**
 * Drag + drop PDF upload. The dashed border marks this as a drop
 * target (EmptyState uses solid), and the focus ring is forwarded
 * from the hidden <input> via `has-[:focus-visible]`.
 *
 * Focus handoff: after a valid pick we move focus to the Remove
 * button (so the next Tab reaches BranchSelect, not back into the
 * input); after Remove we return focus to the file input. A polite
 * live region announces the selection for screen readers.
 */
type Props = {
  file: File | null;
  onChange: (file: File | null) => void;
  errorId?: string;
};

type LocalError = PdfValidationError | { kind: "multiple_files" };

function errorMessage(err: LocalError): string {
  if (err.kind === "multiple_files") return "Drop one PDF at a time.";
  return pdfErrorMessage(err);
}

export function PdfDropzone({ file, onChange, errorId }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const removeRef = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<LocalError | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const generatedErrorId = useId();
  const resolvedErrorId = errorId ?? generatedErrorId;

  // Track the previous file to know when a selection just happened.
  const prevFile = useRef<File | null>(file);
  useEffect(() => {
    if (!prevFile.current && file) removeRef.current?.focus();
    if (prevFile.current && !file) inputRef.current?.focus();
    prevFile.current = file;
  }, [file]);

  const commitFiles = (files: FileList | File[] | null) => {
    if (!files || files.length === 0) {
      setError(null);
      onChange(null);
      return;
    }
    if (files.length > 1) {
      if (inputRef.current) inputRef.current.value = "";
      setError({ kind: "multiple_files" });
      onChange(null);
      return;
    }
    const next = files[0];
    if (!next) {
      setError(null);
      onChange(null);
      return;
    }
    const v = validatePdfFile(next);
    if (v) {
      // Clear the <input> so the same bad file can be re-picked —
      // otherwise onChange wouldn't fire a second time.
      if (inputRef.current) inputRef.current.value = "";
      setError(v);
      onChange(null);
      return;
    }
    setError(null);
    onChange(next);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    commitFiles(e.dataTransfer.files);
  };

  const onDragLeave = (e: React.DragEvent<HTMLLabelElement>) => {
    // Ignore leave events whose relatedTarget is still inside the
    // label — otherwise the drop-highlight flickers while the user
    // moves over the icon / text inside.
    const next = e.relatedTarget;
    if (next instanceof Node && e.currentTarget.contains(next)) return;
    setIsDragging(false);
  };

  const clear = () => {
    if (inputRef.current) inputRef.current.value = "";
    setError(null);
    onChange(null);
  };

  if (file) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <div className="flex items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted">
            <FileTextIcon className="size-4 text-muted-foreground" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={file.name}>
              {file.name}
            </p>
            <p className="tabular text-xs text-muted-foreground">
              {(file.size / (1024 * 1024)).toFixed(1)} MB
            </p>
          </div>
          <button
            ref={removeRef}
            type="button"
            onClick={clear}
            aria-label={`Remove ${file.name}`}
            className="inline-flex h-8 items-center gap-1 rounded-md border border-border bg-background px-2.5 text-xs font-medium hover:bg-muted"
          >
            <XIcon className="size-3.5" aria-hidden />
            Remove
          </button>
        </div>
        <div aria-live="polite" className="sr-only">
          {file.name} selected
        </div>
      </div>
    );
  }

  return (
    <div>
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center rounded-md border border-dashed bg-card px-6 py-12 text-center transition-colors",
          "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-offset-2",
          isDragging ? "border-primary bg-accent/40" : "border-border hover:bg-muted/40",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="sr-only"
          aria-describedby={error ? resolvedErrorId : undefined}
          onChange={(e) => commitFiles(e.target.files)}
        />
        <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
          <UploadCloudIcon className="size-5 text-muted-foreground" aria-hidden />
        </div>
        <p className="text-sm font-medium">Drop a PDF here, or click to choose</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Up to {MAX_UPLOAD_MB} MB, up to {MAX_PAGES} pages. Text or scanned.
        </p>
      </label>
      {error ? <InlineError id={resolvedErrorId}>{errorMessage(error)}</InlineError> : null}
      <div aria-live="polite" className="sr-only" />
    </div>
  );
}
