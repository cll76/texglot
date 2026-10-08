from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from .providers import PROVIDERS, provider_for_url

ROOT = Path(__file__).resolve().parent.parent
SOURCE_CHECKOUT = (ROOT / "pyproject.toml").is_file() and (ROOT / "frontend").is_dir()
DEFAULT_DATA = ROOT / "data" if SOURCE_CHECKOUT else Path.home() / ".texglot"
DATA = Path(
    os.environ.get("TEXGLOT_DATA_DIR", os.environ.get("MOYI_DATA_DIR", DEFAULT_DATA))
).resolve()
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
CONFIG = DATA / "settings.json"
API_FIELDS = {
    "base_url",
    "api_format",
    "model",
    "api_key",
    "concurrency",
    "temperature",
    "timeout",
    "deepl_source_language",
    "deepl_glossary_id",
}


class Settings(BaseModel):
    base_url: str = "https://api.deepseek.com"
    model: str = PROVIDERS["deepseek"]["model"]
    api_key: str = ""
    api_format: Literal["chat_completions", "messages"] = "chat_completions"
    api_profile_id: str = ""
    target_language: str = "简体中文"
    context_guidance: bool = True
    concurrency: int = Field(default=3, ge=1, le=12)
    temperature: float = Field(default=0.2, ge=0, le=1)
    timeout: int = Field(default=180, ge=15, le=600)
    glossary: str = Field(default="", max_length=12000)
    deepl_source_language: str = ""
    deepl_glossary_id: str = ""
    compiler: str = "auto"

    @model_validator(mode="before")
    @classmethod
    def infer_api_format(cls, values):
        if isinstance(values, dict) and "api_format" not in values:
            values = dict(values)
            url = str(values.get("base_url", "")).strip().rstrip("/")
            values["api_format"] = (
                "messages" if url.endswith("/messages") else "chat_completions"
            )
        return values

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        value = value.strip().rstrip("/")
        u = urlsplit(value)
        if (
            u.scheme not in ("https", "http")
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("请输入有效的 API Base URL，不要包含密钥或查询参数")
        if provider_for_url(value) == "deepl":
            if u.scheme != "https" or u.port not in (None, 443):
                raise ValueError("DeepL 请使用官方 HTTPS API 地址")
            if u.path.rstrip("/") not in ("", "/v2", "/v2/translate"):
                raise ValueError("DeepL 请使用官方 HTTPS API 地址")
            return f"https://{u.hostname.lower()}"
        return value.removesuffix("/chat/completions").removesuffix("/messages")

    @field_validator("model")
    @classmethod
    def validate_model(cls, value):
        if not value.strip() or len(value) > 200:
            raise ValueError("请输入模型名称")
        return value.strip()

    @field_validator("deepl_source_language")
    @classmethod
    def validate_deepl_source_language(cls, value):
        value = value.strip().upper()
        if value and not re.fullmatch(r"[A-Z]{2,3}(?:-[A-Z]{2,4})?", value):
            raise ValueError("请输入有效的 DeepL 源语言代码，例如 EN")
        return value

    @field_validator("deepl_glossary_id")
    @classmethod
    def validate_deepl_glossary(cls, value):
        value = value.strip()
        if value and not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value
        ):
            raise ValueError("请输入有效的 DeepL 术语表 ID")
        return value

    @model_validator(mode="after")
    def validate_translation_service(self):
        if provider_for_url(self.base_url) == "deepl":
            self.model = "DeepL"
            if self.deepl_glossary_id and not self.deepl_source_language:
                raise ValueError("使用 DeepL 术语表时，请指定源语言")
        return self

    @field_validator("target_language")
    @classmethod
    def validate_language(cls, value):
        if value not in ("简体中文", "繁體中文", "English"):
            raise ValueError("暂不支持该目标语言")
        return value

    @field_validator("compiler")
    @classmethod
    def validate_compiler(cls, value):
        if value not in ("auto", "tectonic", "xelatex", "lualatex"):
            raise ValueError("无效编译器")
        return value


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(temp, path)


def load_settings() -> Settings:
    data = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if "api_key" not in data:
        provider = provider_for_url(data.get("base_url", "https://api.deepseek.com"))
        env = {
            "deepseek": "DEEPSEEK_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "deepl": "DEEPL_API_KEY",
        }.get(provider)
        if env:
            data["api_key"] = os.environ.get(env, "")
    return Settings(**data)


def public_settings(settings: Settings) -> dict:
    data = settings.model_dump(exclude={"api_key"})
    data["has_api_key"] = bool(settings.api_key)
    return data


def load_connections() -> dict:
    path = CONFIG.with_name("connections.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_api_profiles() -> dict:
    path = CONFIG.with_name("api-profiles.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def public_api_profile(profile: dict) -> dict:
    return {
        **{key: value for key, value in profile.items() if key != "api_key"},
        "has_api_key": bool(profile.get("api_key")),
    }


def api_profiles() -> list[dict]:
    return [public_api_profile(profile) for profile in load_api_profiles().values()]


def get_api_profile(profile_id: str) -> dict:
    profiles = load_api_profiles()
    profile = profiles.get(profile_id) or next(
        (p for p in profiles.values() if p["name"] == profile_id), None
    )
    if profile is None:
        raise ValueError("API 配置不存在")
    return profile


def save_api_profile(name: str, values: dict, profile_id: str = "") -> dict:
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("请输入 1 至 80 字符的 API 配置名称")
    profiles = load_api_profiles()
    if profile_id and profile_id not in profiles:
        raise ValueError("API 配置不存在")
    if any(p["name"] == name and p["id"] != profile_id for p in profiles.values()):
        raise ValueError("API 配置名称已存在，请使用其他名称")
    previous = load_settings()
    if profile_id:
        previous = Settings(
            **(
                previous.model_dump()
                | {
                    key: profiles[profile_id].get(
                        key, Settings.model_fields[key].default
                    )
                    for key in API_FIELDS
                }
                | {"api_profile_id": profile_id}
            )
        )
    settings = merge_settings(previous, values)
    profile_id = profile_id or uuid.uuid4().hex[:16]
    profile = {
        "id": profile_id,
        "name": name,
        **settings.model_dump(include=API_FIELDS),
    }
    profiles[profile_id] = profile
    atomic_json(CONFIG.with_name("api-profiles.json"), profiles)
    return public_api_profile(profile)


def delete_api_profile(profile_id: str) -> Settings:
    profiles = load_api_profiles()
    if profile_id not in profiles:
        raise ValueError("API 配置不存在")
    del profiles[profile_id]
    atomic_json(CONFIG.with_name("api-profiles.json"), profiles)
    settings = load_settings()
    if settings.api_profile_id == profile_id:
        settings.api_profile_id = ""
        atomic_json(CONFIG, settings.model_dump())
    return settings


def provider_options() -> list[dict]:
    connections = load_connections()
    current = load_settings()
    connections.pop(current.base_url, None)
    connections[current.base_url] = current.model_dump(include=API_FIELDS)
    result = []
    for provider, preset in PROVIDERS.items():
        saved = [
            v for url, v in connections.items() if provider_for_url(url) == provider
        ]
        chosen = saved[-1] if saved else preset
        connection = (
            Settings(base_url=chosen["base_url"]) if chosen["base_url"] else None
        )
        result.append(
            {
                **preset,
                "id": provider,
                "base_url": connection.base_url if connection else "",
                "api_format": chosen.get(
                    "api_format",
                    connection.api_format if connection else "chat_completions",
                ),
                "model": chosen["model"],
                "deepl_source_language": chosen.get("deepl_source_language", ""),
                "deepl_glossary_id": chosen.get("deepl_glossary_id", ""),
                "has_api_key": bool(chosen.get("api_key")),
                "saved": bool(saved),
            }
        )
    return result


def merge_settings(old_settings: Settings, values: dict) -> Settings:
    old = old_settings.model_dump()
    values = dict(values)
    values.pop("has_api_key", None)
    profile_id = values.get(
        "api_profile_id", "" if values.get("provider") else old["api_profile_id"]
    )
    if profile_id:
        profile = get_api_profile(profile_id)
        old.update(
            {
                key: profile.get(key, Settings.model_fields[key].default)
                for key in API_FIELDS
            }
        )
        values["api_profile_id"] = profile["id"]
    provider = values.pop("provider", None)
    if provider is not None:
        if provider not in PROVIDERS:
            raise ValueError("不支持的模型服务商")
        preset = next(p for p in provider_options() if p["id"] == provider)
        values = {
            "base_url": preset["base_url"],
            "model": preset["model"],
            "api_format": preset["api_format"],
            "api_profile_id": "",
            "deepl_source_language": preset["deepl_source_language"],
            "deepl_glossary_id": preset["deepl_glossary_id"],
        } | values
        if not values["base_url"]:
            raise ValueError("请填写百炼控制台提供的 OpenAI 兼容地址")
        if not values.get("api_key"):
            source_url = preset["base_url"]
            old["api_key"] = (
                old_settings.api_key
                if source_url == old_settings.base_url
                else load_connections().get(source_url, {}).get("api_key", "")
            )
    if "base_url" in values and "api_format" not in values:
        url = values["base_url"].strip().rstrip("/")
        if url.endswith(("/messages", "/chat/completions")):
            values["api_format"] = Settings(base_url=url).api_format
    if not values.get("api_key"):
        values.pop("api_key", None)
    if values.pop("clear_api_key", False):
        old["api_key"] = ""
    return Settings(**(old | values))


def save_settings(values: dict) -> Settings:
    previous = load_settings()
    settings = merge_settings(previous, values)
    connections = load_connections()
    for connection in (previous, settings):
        connections.pop(connection.base_url, None)
        connections[connection.base_url] = connection.model_dump(include=API_FIELDS)
    if settings.api_profile_id:
        profiles = load_api_profiles()
        profiles[settings.api_profile_id].update(
            settings.model_dump(include=API_FIELDS)
        )
        atomic_json(CONFIG.with_name("api-profiles.json"), profiles)
    atomic_json(CONFIG.with_name("connections.json"), connections)
    atomic_json(CONFIG, settings.model_dump())
    return settings
