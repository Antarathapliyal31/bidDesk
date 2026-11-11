# public/samples — TEMPORARY

Vite serves this directory at `/samples/*`. The New-bid page's
"Start from a sample" cards fetch PDFs from here before upload.

**These files land when `make samples` runs on the backend
(`backend/samples/make_samples.py`, SPEC §12.1).** Until then the
fetch 404s and the UI shows a toast ("Couldn't load the sample PDF.").
Once the Phase 5 merge wires `GET /api/samples/{key}/file`, this
directory is deleted.

Expected files:
- `rfp_clean.pdf`
- `rfp_messy.pdf`
- `rfp_scanned.pdf`
