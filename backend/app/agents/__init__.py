"""Three agents: Document Analyst, Pricing Agent, Orchestrator.

Each lives in its own subpackage and registers an `AgentSpec` at
startup (`app.runtime.delegate.register_agent`). The orchestrator
reaches analyst / pricing only through `delegate()` — the rule
that makes the boundary real (SPEC §4.1).
"""
