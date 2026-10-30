"""The model client: first configured model answers; failures fall through to the next."""

import json

import httpx
import pytest

from wind_monitor.agent.llm import (
    ChatClient,
    ModelRef,
    ModelUnavailableError,
    client_for,
    models_for,
)

PLANNER = [ModelRef("meta", "muse-spark-1.3"), ModelRef("baseten", "muse-spark-open")]
KEYS = {"MODEL_API_KEY": "meta-key", "BASETEN_API_KEY": "baseten-key"}


def _reply(text: str = "hello") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"total_tokens": 12},
        },
    )


def _client(handler, env=KEYS, models=PLANNER) -> tuple[ChatClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(record))
    return ChatClient(models, env, http=http), seen


def test_parse_and_job_defaults():
    assert ModelRef.parse("baseten:meta-models/muse-spark-open-1026") == ModelRef(
        "baseten", "meta-models/muse-spark-open-1026"
    )
    assert str(models_for("planner", {})[0]) == "meta:muse-spark-1.3"
    assert [str(m) for m in models_for("watcher", {"WATCHER_MODELS": "baseten:x, meta:y"})] == [
        "baseten:x",
        "meta:y",
    ]
    for bad in ("muse-spark-1.3", "openai:gpt", "meta:"):
        with pytest.raises(ValueError):
            ModelRef.parse(bad)


def test_first_configured_model_answers_with_its_key():
    client, seen = _client(lambda r: _reply("hi"))
    result = client.chat([{"role": "user", "content": "hello"}], temperature=0)
    assert result.model == "meta:muse-spark-1.3"
    assert result.message["content"] == "hi"
    assert result.usage == {"total_tokens": 12} and result.failed == []
    assert str(seen[0].url) == "https://api.meta.ai/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer meta-key"
    body = json.loads(seen[0].content)
    assert body["model"] == "muse-spark-1.3" and body["temperature"] == 0
    assert "tools" not in body


def test_models_without_a_key_are_skipped():
    client, seen = _client(lambda r: _reply(), env={"BASETEN_API_KEY": "b"})
    assert [str(m) for m in client.available()] == ["baseten:muse-spark-open"]
    assert client.chat([]).model == "baseten:muse-spark-open"
    assert seen[0].url.host == "inference.baseten.co"


@pytest.mark.parametrize(
    "failure",
    [
        lambda r: httpx.Response(429, text="slow down"),
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, text="<html>maintenance</html>"),
    ],
)
def test_failure_falls_through_to_the_next_model(failure):
    def handler(request):
        return failure(request) if request.url.host == "api.meta.ai" else _reply("backup")

    client, _ = _client(handler)
    result = client.chat([], tools=[{"type": "function", "function": {"name": "t"}}])
    assert result.model == "baseten:muse-spark-open"
    assert result.message["content"] == "backup"
    assert result.failed[0]["model"] == "meta:muse-spark-1.3"


def test_network_error_falls_through():
    def handler(request):
        if request.url.host == "api.meta.ai":
            raise httpx.ConnectError("no route", request=request)
        return _reply()

    client, _ = _client(handler)
    assert client.chat([]).failed[0]["error"].startswith("ConnectError")


def test_all_failing_is_an_error_listing_each_attempt():
    client, _ = _client(lambda r: httpx.Response(503, text="down"))
    with pytest.raises(ModelUnavailableError) as info:
        client.chat([])
    tried = [f["model"] for f in info.value.failed]
    assert tried == ["meta:muse-spark-1.3", "baseten:muse-spark-open"]


def test_nothing_configured_names_the_keys_to_set():
    client, _ = _client(lambda r: _reply(), env={})
    with pytest.raises(ModelUnavailableError, match="BASETEN_API_KEY.*MODEL_API_KEY"):
        client.chat([])


def test_self_hosted_model_needs_its_url():
    models = [ModelRef("modal", "muse-glimmer")]
    client, seen = _client(lambda r: _reply(), env={"MODAL_GLIMMER_KEY": "k"}, models=models)
    assert client.available() == []
    env = {"MODAL_GLIMMER_KEY": "k", "MODAL_GLIMMER_URL": "https://me--glimmer.modal.run/v1/"}
    client, seen = _client(lambda r: _reply(), env=env, models=models)
    client.chat([])
    assert str(seen[0].url) == "https://me--glimmer.modal.run/v1/chat/completions"


def test_client_for_a_job_reads_the_environment():
    env = {"PLANNER_MODELS": "baseten:m", "BASETEN_API_KEY": "b"}
    assert [str(m) for m in client_for("planner", env).available()] == ["baseten:m"]
