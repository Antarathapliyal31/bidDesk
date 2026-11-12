"""Background run execution (SPEC §10.5).

`start_pipeline(run_id, ...)` kicks `run_orchestrator` off as
an `asyncio.Task` and returns immediately so the HTTP layer can
respond 201 before the model work begins. Tasks are tracked in
an app-level dict so they aren't GC'd + can be cancelled on
shutdown.

Lifespan contract (lives in `app/main.py`):
  - Startup: any `runs.status='running'` row from a previous
    process gets flipped to `interrupted` and a terminal
    `run.finished(status=interrupted)` event is written.
  - Shutdown: cancel every in-flight task; the DB row + events
    stay — the next startup will resolve them.

Browser disconnects never affect the run. Events keep landing
on the bus, which persists them on disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import (
    MAX_ORCHESTRATOR_TURNS,
    RunPlan,
    run_orchestrator,
)
from app.agents.orchestrator.tools import register_pricing_for_run
from app.catalog.semantic_layer import SemanticBundle
from app.config import Settings
from app.contracts import AgentName
from app.contracts.bid import BranchInfo
from app.db import repo
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext
from app.runtime.events import EventBus
from app.runtime.llm import AnthropicLLM, FakeLLM

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineInputs:
    run_id: str
    branch: BranchInfo
    plan: RunPlan


class RunRegistry:
    """Owns in-flight asyncio tasks keyed by run_id. Lives on
    `app.state`; the SSE stream looks up tasks here so it can
    close deterministically after `run.finished`."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    def is_running(self, run_id: str) -> bool:
        return run_id in self._tasks

    def all_tasks(self) -> list[asyncio.Task[Any]]:
        return list(self._tasks.values())

    async def cancel_all(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def start_pipeline(
    *,
    inputs: PipelineInputs,
    db: Database,
    bus: EventBus,
    settings: Settings,
    bundle: SemanticBundle,
    registry: RunRegistry,
    fake_llms: dict[str, FakeLLM] | None = None,
) -> asyncio.Task[Any]:
    """Create the background task + return it. `fake_llms`, when
    provided (LLM_MODE=fake path), carries `{analyst, pricing,
    orchestrator}` FakeLLM instances that replace the real
    AnthropicLLMs for the Playwright/offline demo."""

    task = asyncio.create_task(
        _run_pipeline(
            inputs=inputs,
            db=db,
            bus=bus,
            settings=settings,
            bundle=bundle,
            fake_llms=fake_llms,
        ),
        name=f"run-{inputs.run_id}",
    )
    registry.register(inputs.run_id, task)
    return task


async def _run_pipeline(
    *,
    inputs: PipelineInputs,
    db: Database,
    bus: EventBus,
    settings: Settings,
    bundle: SemanticBundle,
    fake_llms: dict[str, FakeLLM] | None,
) -> None:
    """Build the budget + LLM clients, dispatch to
    `run_orchestrator`, and finalise the run row. Every path ends
    in `run.finished` + a terminal `runs.status` (completed /
    partial / failed / interrupted)."""

    bus_run_id = inputs.run_id
    try:
        await bus.emit(
            type="run.started",
            run_id=bus_run_id,
            agent=None,
            payload={
                "input_kind": inputs.plan.kind,
                "input_name": (Path(inputs.plan.pdf_path).name if inputs.plan.pdf_path else "text"),
                "page_count": None,  # analyst fills via span later
                "branch_id": inputs.plan.branch_id,
            },
        )

        budget = Budget(
            turns=MAX_ORCHESTRATOR_TURNS,
            tokens=settings.run_token_budget,
            deadline_s=float(settings.run_timeout_s),
        )
        budget.start()
        agent_ctx = AgentContext(run_id=inputs.run_id, bus=bus, budget=budget)

        # Agents are registered PER RUN with their LLMs. The
        # analyst and pricing registries clear between runs.
        if fake_llms is not None:
            register_default_analyst(fake_llms.get("analyst") or FakeLLM(responses=[]))
            register_pricing_for_run(
                fake_llms.get("pricing") or FakeLLM(responses=[]),
                bundle=bundle,
                catalog_db_path=settings.catalog_db_path,
            )
            orchestrator_llm: Any = fake_llms.get("orchestrator") or FakeLLM(responses=[])
        else:
            register_default_analyst(AnthropicLLM(db=db))
            register_pricing_for_run(
                AnthropicLLM(db=db),
                bundle=bundle,
                catalog_db_path=settings.catalog_db_path,
            )
            orchestrator_llm = AnthropicLLM(db=db)

        try:
            outcome = await run_orchestrator(
                inputs.plan,
                ctx=agent_ctx,
                db=db,
                bundle=bundle,
                catalog_db_path=settings.catalog_db_path,
                llm=orchestrator_llm,
                branch=inputs.branch,
            )
        except asyncio.CancelledError:
            # Shutdown-triggered cancel. Flip the run row; emit
            # run.finished(interrupted) via the lifespan path.
            await repo.finalize_run_status(
                db, run_id=inputs.run_id, status="interrupted", outcome=None
            )
            raise
        except Exception as exc:
            _logger.exception("pipeline failed", exc_info=exc)
            await repo.finalize_run_status(
                db,
                run_id=inputs.run_id,
                status="failed",
                outcome="error",
                outcome_detail=f"{exc.__class__.__name__}",
            )
            await bus.emit(
                type="run.finished",
                run_id=bus_run_id,
                agent=AgentName.orchestrator,
                payload={
                    "status": "failed",
                    "outcome": "error",
                    "outcome_detail": f"{exc.__class__.__name__}",
                },
            )
            return

        await repo.finalize_run_status(
            db,
            run_id=inputs.run_id,
            status=outcome.status,
            outcome=outcome.outcome,
            outcome_detail=outcome.outcome_detail,
        )
        await bus.emit(
            type="run.finished",
            run_id=bus_run_id,
            agent=AgentName.orchestrator,
            payload={
                "status": outcome.status,
                "outcome": outcome.outcome,
                "outcome_detail": outcome.outcome_detail,
            },
        )
    finally:
        _ = datetime.now(UTC)  # reserved — audit timestamp site
