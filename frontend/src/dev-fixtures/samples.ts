import type { FxSampleInput } from "./types";

/**
 * The four sample inputs defined in SPEC §3 (samples/). PDF variants
 * carry `file_url` that `frontend/public/samples/` serves (see that
 * directory's README); the paste variant carries `text` for the
 * form's textarea.
 *
 * Item counts in `size_label` must match
 * `samples/expected/*.json` — a mismatch means the card's promise
 * doesn't line up with what the stream + priced bid will show.
 */
export const FIXTURE_SAMPLES: FxSampleInput[] = [
  {
    kind: "pdf",
    key: "clean",
    name: "rfp_clean.pdf",
    description: "Text PDF, no conflicts. The happy path.",
    size_label: "3 pages · 14 items",
    file_url: "/samples/rfp_clean.pdf",
  },
  {
    kind: "pdf",
    key: "messy",
    name: "rfp_messy.pdf",
    description: "Text PDF with planted conflicts, gaps, yards, and an injection line.",
    size_label: "8 pages · 26 items",
    file_url: "/samples/rfp_messy.pdf",
  },
  {
    kind: "pdf",
    key: "scanned",
    name: "rfp_scanned.pdf",
    description: "Image-only version of rfp_clean — exercises the vision path.",
    size_label: "3 pages · 14 items",
    file_url: "/samples/rfp_scanned.pdf",
  },
  {
    kind: "paste",
    key: "paste",
    name: "list_paste.txt",
    description: "Short pasted list with abbreviations like LF, SQ, SHT.",
    size_label: "10 items",
    text: [
      "(40) 2x6x16 PT #2",
      "12 SHT OSB 7/16",
      "6 BDL asphalt shingles",
      "60 LF 1/2 drywall",
      "4 rolls housewrap",
      "200 SF R-13 batt insulation",
      "1 BOX 16d common nails",
      "1 BOX joist hangers 2x10",
      "80 LF 2x4x8 SPF stud",
      "12 EA 2x12x14 SYP #2",
    ].join("\n"),
  },
];
