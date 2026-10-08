import json

import httpx
import pytest

from app.config import Settings
from app.latex import MARKER, segments
from app.llm import ProviderError, Translator


async def test_messages_protocol_preserves_prompts_text_blocks_and_usage():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "This is not output text."},
                    {"type": "text", "text": ' {"translation":'},
                    {"type": "text", "text": '"准确的译文。"} '},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 30, "output_tokens": 12},
            },
        )

    translator = Translator(
        Settings(
            base_url="http://proxy.example:23000/v1/messages/",
            api_key="fake-messages-key",
            model="proxy-model",
        )
    )
    await translator.client.aclose()
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        output = await translator.complete(
            [
                {"role": "system", "content": "Translate academic prose."},
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": "A result with ⟪P0000⟫."},
            ],
            max_tokens=321,
            json_output=True,
        )
        assert json.loads(output) == {"translation": "准确的译文。"}
        assert translator.tokens == 42
        assert translator.requests == 1
        request = calls[0]
        assert str(request.url) == "http://proxy.example:23000/v1/messages"
        assert request.headers["x-api-key"] == "fake-messages-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert json.loads(request.content) == {
            "model": "proxy-model",
            "system": "Translate academic prose.\n\nReturn JSON.",
            "messages": [{"role": "user", "content": "A result with ⟪P0000⟫."}],
            "max_tokens": 321,
            "temperature": 0.2,
            "stream": False,
        }
    finally:
        await translator.close()


async def test_messages_translation_and_slot_repair_preserve_protected_content():
    item = segments(r"The model uses $x$ and 2 layers.")[0]

    def handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][0]["content"])
        assert "system" in body
        if "slots" in payload:
            output = json.dumps({key: "译文" for key in payload["slots"]})
        else:
            output = "模型使用⟪P0000⟫和⟪P0001⟫层。"
        return httpx.Response(200, json={"content": [{"type": "text", "text": output}]})

    translator = Translator(Settings(base_url="http://proxy.example/v1/messages"))
    await translator.client.aclose()
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        output = await translator.translate(item)
        assert item.restore(output) == r"模型使用$x$和2层。"
        repaired = await translator.translate_slots(item, allow_line_repair=False)
        assert MARKER.findall(repaired) == MARKER.findall(item.masked)
        assert "$x$" in item.restore(repaired) and "2" in item.restore(repaired)
    finally:
        await translator.close()


async def test_connection_probe_allows_room_for_model_reasoning():
    def handler(request):
        budget = json.loads(request.content)["max_tokens"]
        truncated = budget < 200
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "实验证实了假设。"}],
                "stop_reason": "max_tokens" if truncated else "end_turn",
                "usage": {"input_tokens": 40, "output_tokens": min(budget, 200)},
            },
        )

    translator = Translator(Settings(base_url="http://proxy.example/v1/messages"))
    await translator.client.aclose()
    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await translator.test()
        assert result["ok"] and result["message"] == "实验证实了假设。"
        assert translator.requests == 1
    finally:
        await translator.close()


@pytest.mark.parametrize(
    "response,error,message",
    [
        ({"content": None}, ProviderError, "Messages 接口"),
        ({"content": [None]}, ProviderError, "Messages 接口"),
        ({"content": [{"type": "text", "text": None}]}, ProviderError, "Messages 接口"),
        ({"content": []}, ValueError, "有效文本"),
        (
            {
                "content": [{"type": "text", "text": "An incomplete answer"}],
                "stop_reason": "max_tokens",
            },
            ValueError,
            "截断",
        ),
    ],
)
async def test_invalid_or_truncated_messages_are_rejected(response, error, message):
    translator = Translator(Settings(base_url="http://proxy.example/v1/messages"))
    await translator.client.aclose()
    translator.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    try:
        with pytest.raises(error, match=message):
            await translator.complete([{"role": "user", "content": "Translate."}])
    finally:
        await translator.close()


@pytest.mark.parametrize(
    "usage,expected", [(None, 0), ({"input_tokens": "invalid", "output_tokens": 5}, 5)]
)
async def test_optional_messages_usage_does_not_discard_valid_text(usage, expected):
    translator = Translator(Settings(base_url="http://proxy.example/v1/messages"))
    await translator.client.aclose()
    translator.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"content": [{"type": "text", "text": "译文。"}], "usage": usage},
            )
        )
    )
    try:
        assert (
            await translator.complete([{"role": "user", "content": "Translate."}])
            == "译文。"
        )
        assert translator.tokens == expected
    finally:
        await translator.close()
