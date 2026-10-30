"""The agent loop, driven by a scripted fake model (no network, no credits).

With the synthetic data at 18:00 on Thu 8 Jan, a 14 kWh / 7 kW charge goes in 02:00-04:00 on
Friday at 15,000 MW expected, against 10,000 MW if charged straight away (50% more).
"""

import json

import pytest
from wind_fixtures import NOW, synthetic_wind

from wind_monitor.agent.llm import ChatResult, ModelUnavailableError
from wind_monitor.agent.runner import run_agent

QUESTION = "I need 14 kWh at 7 kW, when should I charge?"
GOOD = "Charge 02:00-04:00 on Friday: about 15,000 MW of wind, 50% more than charging now."


class FakeModel:
    """Replies in order; records the messages it was sent."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.sent: list[list[dict]] = []

    def chat(self, messages, tools=None, **params) -> ChatResult:
        self.sent.append(json.loads(json.dumps(messages)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(reply, "fake:model", 0.01)


def call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


def say(text: str) -> dict:
    return {"role": "assistant", "content": text}


PLAN_CALL = call("plan_charge", {"energy_kwh": 14, "power_kw": 7})


def _run(model: FakeModel):
    return run_agent(QUESTION, synthetic_wind(), NOW, model)


def test_tool_call_then_a_checked_answer():
    model = FakeModel(PLAN_CALL, say(GOOD))
    run = _run(model)
    assert run.verified and run.error is None and run.rewrites == 0
    assert run.answer == GOOD
    assert run.factcheck["summary"] == "facts checked 4/4"
    assert run.models == ["fake:model", "fake:model"]
    assert run.plan["charge_windows"] == [{"from": "Fri 02:00", "to": "Fri 04:00", "kwh": 14.0}]
    assert run.schedule == [
        {
            "start": "2026-01-09T02:00:00+00:00",
            "end": "2026-01-09T04:00:00+00:00",
            "energy_kwh": 14.0,
        }
    ]
    assert [c["name"] for c in run.tool_calls] == ["plan_charge"]
    # The model saw the current time, then the tool result tied to its call id.
    assert "Thu 08 Jan 18:00" in model.sent[0][1]["content"]
    tool_message = model.sent[1][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert json.loads(tool_message["content"])["expected_wind_while_charging_mw"] == 15_000


def test_a_misquoted_figure_gets_one_rewrite():
    model = FakeModel(PLAN_CALL, say("Charge 02:00-04:00 at about 18,000 MW."), say(GOOD))
    run = _run(model)
    assert run.verified and run.rewrites == 1 and run.answer == GOOD
    assert "'18,000 MW'" in model.sent[2][-1]["content"]


def test_still_wrong_after_the_rewrite_is_marked_unverified():
    wrong = say("Charge 21:00-23:00 at about 18,000 MW.")
    run = _run(FakeModel(PLAN_CALL, wrong, wrong))
    assert not run.verified and run.rewrites == 1
    assert run.factcheck["summary"] == "3 facts don't match"
    assert run.plan is not None  # kept for the log, but an unverified run is never dispatched


def test_a_tool_error_is_fixed_by_the_model():
    bad = call("plan_charge", {"energy_kwh": 14, "power_kw": 7, "deadline": "tomorrow"}, "c1")
    run = _run(FakeModel(bad, PLAN_CALL, say(GOOD)))
    assert run.verified
    assert "error" in run.tool_calls[0]["result"] and "error" not in run.tool_calls[1]["result"]
    assert run.plan["feasible"] is True


def test_answer_without_tools_and_figures_is_verified_but_has_no_plan():
    run = _run(FakeModel(say("Plug the car in and I will plan it.")))
    assert run.verified and run.plan is None and run.factcheck["summary"] == "no figures to check"


def test_no_model_available_is_reported():
    failure = ModelUnavailableError("Every configured model failed", [{"model": "meta:x"}])
    run = _run(FakeModel(failure))
    assert run.answer is None and not run.verified
    assert run.error == "Every configured model failed"
    assert run.failed_models == [{"model": "meta:x"}]


def test_a_model_that_never_stops_calling_tools_is_cut_off():
    model = FakeModel(*[call("check_recent_wind", {}, f"c{i}") for i in range(10)])
    run = run_agent(QUESTION, synthetic_wind(), NOW, model, max_rounds=3)
    assert run.error == "No final answer after 3 model calls" and len(run.tool_calls) == 3


@pytest.mark.parametrize(
    "arguments", ['{"energy_kwh": 14, "power_kw": 7}', {"energy_kwh": 14, "power_kw": 7}]
)
def test_arguments_as_text_or_object(arguments):
    reply = call("plan_charge", {})
    reply["tool_calls"][0]["function"]["arguments"] = arguments
    assert _run(FakeModel(reply, say(GOOD))).verified
