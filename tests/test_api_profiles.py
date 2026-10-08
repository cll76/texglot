import json

import httpx
import pytest

from app import config
from app.config import Settings, load_settings, merge_settings, save_settings
from app.llm import Translator
from app.translation import cache_settings


@pytest.fixture
def local_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG", tmp_path / "settings.json")
    return tmp_path


def test_deepl_profiles_restore_their_own_source_language_and_glossary(local_config):
    saved = []
    for name, language, glossary in (
        ("DeepL English", "EN", "12345678-1234-1234-1234-123456789abc"),
        ("DeepL German", "DE", "abcdef01-1234-1234-1234-123456789abc"),
    ):
        profile = config.save_api_profile(
            name,
            {
                "provider": "deepl",
                "api_key": name + "-test-key",
                "deepl_source_language": language,
                "deepl_glossary_id": glossary,
            },
        )
        saved.append(profile)
    for profile in reversed(saved):
        current = save_settings({"api_profile_id": profile["id"]})
        assert current.model == "DeepL"
        assert current.deepl_source_language == profile["deepl_source_language"]
        assert current.deepl_glossary_id == profile["deepl_glossary_id"]
        assert current.api_key == profile["name"] + "-test-key"
    save_settings({"provider": "deepseek"})
    current = save_settings({"provider": "deepl"})
    assert current.deepl_glossary_id == saved[0]["deepl_glossary_id"]


def test_existing_llm_profile_clears_deepl_options_when_selected(local_config):
    profile = config.save_api_profile(
        "Existing proxy",
        {"base_url": "http://proxy.example/v1", "api_key": "profile-test-key"},
    )
    profiles = config.load_api_profiles()
    profiles[profile["id"]].pop("deepl_source_language")
    profiles[profile["id"]].pop("deepl_glossary_id")
    config.atomic_json(config.CONFIG.with_name("api-profiles.json"), profiles)
    save_settings(
        {
            "provider": "deepl",
            "api_key": "deepl-test-key",
            "deepl_source_language": "EN",
            "deepl_glossary_id": "12345678-1234-1234-1234-123456789abc",
        }
    )
    current = save_settings({"api_profile_id": profile["id"]})
    assert current.api_key == "profile-test-key"
    assert current.deepl_source_language == current.deepl_glossary_id == ""


def test_translation_caches_distinguish_protocols_for_the_same_proxy():
    chat = Settings(base_url="http://proxy.example/v1", api_format="chat_completions")
    messages = Settings(base_url=chat.base_url, api_format="messages")
    assert cache_settings(chat, "") != cache_settings(messages, "")


async def test_local_pdf_with_deepl_reports_supported_inputs_before_model_requests():
    from app.jobs import JobManager
    from app.llm import ProviderError

    with pytest.raises(ProviderError, match="本地 PDF"):
        await JobManager.pipeline(
            None,
            {"id": "not-created", "kind": "pdf"},
            Settings(base_url="https://api.deepl.com", api_key="deepl-test-key"),
        )


async def test_named_profiles_share_address_and_keep_their_own_protocol_and_key(
    local_config,
):
    from app.main import app

    original = save_settings({"api_key": "original-test-key"})
    base = "http://proxy.example:23000/v1"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        saved = []
        for name, protocol, key in (
            ("Chat proxy", "chat_completions", "chat-test-key"),
            ("Claude proxy", "messages", "messages-test-key"),
        ):
            response = await client.post(
                "/api/profiles",
                json={
                    "name": name,
                    "settings": {
                        "base_url": base,
                        "model": name,
                        "api_format": protocol,
                        "api_key": key,
                        "concurrency": 6,
                        "timeout": 240,
                    },
                },
            )
            assert response.status_code == 201
            profile = response.json()
            assert profile["has_api_key"] and "api_key" not in profile
            saved.append(profile)
        assert load_settings() == original  # Saving a preset does not activate it.
        for profile, key in zip(saved, ("chat-test-key", "messages-test-key")):
            response = await client.put(
                "/api/settings",
                json={
                    "api_profile_id": profile["id"],
                    "api_key": "",
                },
            )
            assert response.status_code == 200
            current = load_settings()
            assert current.base_url == base
            assert current.api_format == profile["api_format"]
            assert current.api_key == key
            assert current.concurrency == 6 and current.timeout == 240
            assert current.api_profile_id == profile["id"]
            by_name = merge_settings(current, {"api_profile_id": profile["name"]})
            assert by_name.api_profile_id == profile["id"] and by_name.api_key == key
        listed = (await client.get("/api/profiles")).json()
        public = json.dumps(listed)
        assert len(listed) == 2
        assert "test-key" not in public and '"api_key"' not in public
        assert (
            json.loads(config.CONFIG.read_text())["api_profile_id"] == saved[-1]["id"]
        )
        switched = save_settings({"provider": "deepseek"})
        assert switched.api_profile_id == "" and switched.api_key == "original-test-key"
        assert config.get_api_profile(saved[-1]["id"])["api_key"] == "messages-test-key"


async def test_profile_edits_and_save_as_keep_stored_key_without_returning_it(
    local_config,
):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = (
            await client.post(
                "/api/profiles",
                json={
                    "name": "First",
                    "settings": {
                        "base_url": "http://proxy.example/v1",
                        "model": "first-model",
                        "api_key": "profile-test-key",
                        "api_format": "messages",
                    },
                },
            )
        ).json()
        updated = await client.put(
            f"/api/profiles/{first['id']}",
            json={
                "name": "Renamed",
                "settings": {
                    **first,
                    "api_profile_id": first["id"],
                    "base_url": "http://new-proxy.example/v1",
                    "api_key": "",
                },
            },
        )
        assert updated.status_code == 200 and updated.json()["has_api_key"]
        copied = await client.post(
            "/api/profiles",
            json={
                "name": "Copy",
                "settings": {"api_profile_id": first["id"], "api_key": ""},
            },
        )
        assert copied.status_code == 201
        copy = copied.json()
        assert copy["id"] != first["id"]
        assert copy["base_url"] == "http://new-proxy.example/v1"
        assert (
            merge_settings(load_settings(), {"api_profile_id": copy["id"]}).api_key
            == "profile-test-key"
        )
        assert "profile-test-key" not in updated.text + copied.text
        await client.put("/api/settings", json={"api_profile_id": copy["id"]})
        await client.put(
            "/api/settings",
            json={
                "api_profile_id": copy["id"],
                "api_key": "replacement-test-key",
            },
        )
        assert config.get_api_profile(copy["id"])["api_key"] == "replacement-test-key"
        deleted = await client.delete(f"/api/profiles/{copy['id']}")
        assert deleted.status_code == 200 and deleted.json()["api_profile_id"] == ""
        assert load_settings().api_key == "replacement-test-key"
        assert len((await client.get("/api/profiles")).json()) == 1


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
async def test_profile_name_must_be_usable(local_config, name):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/profiles", json={"name": name, "settings": {}}
        )
        assert response.status_code == 400
        assert not config.api_profiles()


@pytest.mark.parametrize(
    "protocol,path",
    [("chat_completions", "/chat/completions"), ("messages", "/messages")],
)
async def test_explicit_protocol_selects_request_path_for_the_same_base(protocol, path):
    calls = []
    translator = Translator(
        Settings(base_url="http://proxy.example/v1", api_format=protocol)
    )
    await translator.client.aclose()

    def handler(request):
        calls.append(request)
        data = (
            {"content": [{"type": "text", "text": "译文"}]}
            if protocol == "messages"
            else {"choices": [{"message": {"content": "译文"}}]}
        )
        return httpx.Response(200, json=data)

    translator.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (
            await translator.complete([{"role": "user", "content": "Translate."}])
            == "译文"
        )
        assert str(calls[0].url) == "http://proxy.example/v1" + path
    finally:
        await translator.close()
