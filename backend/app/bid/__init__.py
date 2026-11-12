"""Bid assembly + edits package (SPEC §9).

Code-only. The orchestrator submits intent (summary +
assumptions + contractor questions) through `submit_bid`; this
package turns the run's recorded items, triage, requirements,
price_results, and conflicts into a validated `Bid` via
`assemble.build_bid()`. `totals.py` holds the extended/tax
arithmetic (half-up once per bid, DECISIONS #11, SPEC §13).
"""
