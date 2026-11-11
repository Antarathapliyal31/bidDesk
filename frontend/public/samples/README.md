# public/samples — TEMPORARY

Vite serves this directory at `/samples/*`. The New-bid page's
"Start from a sample" cards fetch PDFs from here before upload.

Files are **copies** of `backend/samples/*.pdf` + `list_paste.txt`.
They're committed so the demo runs offline. Keep them in sync with
`make samples-fe` — `make samples` calls it automatically after
regenerating the PDFs, so the usual workflow doesn't need to touch
this directory. A drift check in
`tests/public-samples-drift.test.ts` compares sha256 against the
backend originals and fails loudly on mismatch.

Files:
- `rfp_clean.pdf`
- `rfp_messy.pdf`
- `rfp_scanned.pdf`
- `list_paste.txt`

Once the Phase 5 merge wires `GET /api/samples/{key}/file`, this
directory (and the Makefile target + drift test) is deleted.
