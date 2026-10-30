"""HTTP routes for the agent: ask it something, and read the log of what it did."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from .agent.llm import ChatClient, client_for
from .agent.runner import run_agent
from .agent.runs import RunLog, get_run_log
from .planning_api import ClockDep, PlanningDataDep
from .schemas import AgentRunOut, AgentRunSummary, AskRequest

router = APIRouter(prefix="/api/agent")


@lru_cache(maxsize=1)
def get_planner() -> ChatClient:
    """The planner's model client (models and keys from the environment)."""
    return client_for("planner")


PlannerDep = Annotated[ChatClient, Depends(get_planner)]
RunLogDep = Annotated[RunLog, Depends(get_run_log)]


@router.post("/ask", response_model=AgentRunOut)
def ask(
    request: AskRequest,
    data: PlanningDataDep,
    clock: ClockDep,
    planner: PlannerDep,
    log: RunLogDep,
) -> dict:
    """Ask the agent. It calls the planning tools, answers, and has every figure fact-checked.
    The full record (models, tool calls, check) is returned and kept in the run log."""
    if not planner.available():
        raise HTTPException(503, "No model is configured: set MODEL_API_KEY or BASETEN_API_KEY")
    run = run_agent(request.question, data, clock.now(), planner, mode=clock.mode)
    log.add(run)
    if run.answer is None:
        raise HTTPException(503, f"The agent could not answer: {run.error}")
    return run.as_dict()


@router.get("/runs", response_model=list[AgentRunSummary])
def recent_runs(log: RunLogDep, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[dict]:
    return [
        {
            "id": r.id,
            "question": r.question,
            "as_of": r.as_of,
            "answer": r.answer,
            "verified": r.verified,
            "summary": r.factcheck["summary"] if r.factcheck else None,
            "models": r.models,
            "seconds": r.seconds,
        }
        for r in log.recent(limit)
    ]


@router.get("/runs/{run_id}", response_model=AgentRunOut)
def one_run(run_id: str, log: RunLogDep) -> dict:
    run = log.get(run_id)
    if run is None:
        raise HTTPException(404, "No such run (the log keeps the last 200)")
    return run.as_dict()
