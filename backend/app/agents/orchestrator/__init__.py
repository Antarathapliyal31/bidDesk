"""Orchestrator — judgment + coordination; no data access (SPEC §4.1).

Hand-written tool loop (`loop.py`). Reaches the analyst via
`delegate(AgentName.document_analyst, ...)`. Owns no SQL and no
raw document text; everything document-side passes through
`Spotlighted`.
"""
