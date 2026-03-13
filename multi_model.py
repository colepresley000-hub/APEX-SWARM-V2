"""
APEX SWARM - Multi-Model Router
=================================
Route agent tasks to any LLM provider. Users pick the model per-agent or globally.

Supported providers:
  - Anthropic (Claude 4.6, Sonnet 4.5, Haiku 4.5)
  - OpenAI (GPT-5.3, GPT-4o, o1, o3-mini)
  - Google (Gemini 2.5 Pro/Flash)
  - Groq (Llama 3, Mixtral — fast inference)
  - DeepSeek (DeepSeek-V3, DeepSeek-R1)
  - Mistral (Large, Medium)
  - OpenRouter (unified gateway to 100+ models)
  - Ollama (local models)
  - xAI (Grok 3)

All providers normalized to a common interface.

File: multi_model.py
"""

import json
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("apex-swarm")


# ─── PROVIDER CONFIGS ────────────────────────────────────

PROVIDERS = {
    "anthropic": {
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com/v1/messages",
        "env_key": "ANTHROPIC_API_KEY",
        "format": "anthropic",
        "models": {
            "claude-opus-4-6": {"name": "Claude Opus 4.6", "context": 200000, "vision": True, "cost_1m_in": 15.0, "cost_1m_out": 75.0},
            "claude-sonnet-4-5-20250929": {"name": "Claude Sonnet 4.5", "context": 200000, "vision": True, "cost_1m_in": 3.0, "cost_1m_out": 15.0},
            "claude-haiku-4-5": {"name": "Claude Haiku 4.5", "context": 200000, "vision": True, "cost_1m_in": 0.80, "cost_1m_out": 4.0},
            "claude-haiku-4-5-20251001": {"name": "Claude Haiku 4.5", "context": 200000, "vision": True, "cost_1m_in": 0.80, "cost_1m_out": 4.0},
        },
        "default": "claude-haiku-4-5",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "env_key": "OPENAI_API_KEY",
        "format": "openai",
        "models": {
            "gpt-4o": {"name": "GPT-4o", "context": 128000, "vision": True, "cost_1m_in": 2.5, "cost_1m_out": 10.0},
            "gpt-4o-mini": {"name": "GPT-4o Mini", "context": 128000, "vision": True, "cost_1m_in": 0.15, "cost_1m_out": 0.60},
            "o3-mini": {"name": "o3-mini", "context": 200000, "vision": False, "cost_1m_in": 1.10, "cost_1m_out": 4.40},
        },
        "default": "gpt-4o-mini",
    },
    "google": {
        "name": "Google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "env_key": "GOOGLE_API_KEY",
        "format": "google",
        "models": {
            "gemini-2.5-pro": {"name": "Gemini 2.5 Pro", "context": 1000000, "vision": True, "cost_1m_in": 1.25, "cost_1m_out": 10.0},
            "gemini-2.5-flash": {"name": "Gemini 2.5 Flash", "context": 1000000, "vision": True, "cost_1m_in": 0.15, "cost_1m_out": 0.60},
        },
        "default": "gemini-2.5-flash",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "env_key": "GROQ_API_KEY",
        "format": "openai",
        "models": {
            "llama-3.3-70b-versatile": {"name": "Llama 3.3 70B", "context": 128000, "vision": False, "cost_1m_in": 0.59, "cost_1m_out": 0.79},
            "llama-3.1-8b-instant": {"name": "Llama 3.1 8B", "context": 128000, "vision": False, "cost_1m_in": 0.05, "cost_1m_out": 0.08},
            "mixtral-8x7b-32768": {"name": "Mixtral 8x7B", "context": 32768, "vision": False, "cost_1m_in": 0.24, "cost_1m_out": 0.24},
        },
        "default": "llama-3.3-70b-versatile",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "env_key": "DEEPSEEK_API_KEY",
        "format": "openai",
        "models": {
            "deepseek-chat": {"name": "DeepSeek V3", "context": 64000, "vision": False, "cost_1m_in": 0.27, "cost_1m_out": 1.10},
            "deepseek-reasoner": {"name": "DeepSeek R1", "context": 64000, "vision": False, "cost_1m_in": 0.55, "cost_1m_out": 2.19},
        },
        "default": "deepseek-chat",
    },
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1/chat/completions",
        "env_key": "MISTRAL_API_KEY",
        "format": "openai",
        "models": {
            "mistral-large-latest": {"name": "Mistral Large", "context": 128000, "vision": False, "cost_1m_in": 2.0, "cost_1m_out": 6.0},
            "mistral-medium-latest": {"name": "Mistral Medium", "context": 128000, "vision": False, "cost_1m_in": 0.40, "cost_1m_out": 2.0},
        },
        "default": "mistral-large-latest",
    },
    "xai": {
        "name": "xAI",
        "base_url": "https://api.x.ai/v1/chat/completions",
        "env_key": "XAI_API_KEY",
        "format": "openai",
        "models": {
            "grok-3": {"name": "Grok 3", "context": 131072, "vision": True, "cost_1m_in": 3.0, "cost_1m_out": 15.0},
            "grok-3-mini": {"name": "Grok 3 Mini", "context": 131072, "vision": False, "cost_1m_in": 0.30, "cost_1m_out": 0.50},
        },
        "default": "grok-3-mini",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "env_key": "OPENROUTER_API_KEY",
        "format": "openai",
        "models": {
            "anthropic/claude-sonnet-4": {"name": "Claude Sonnet 4 (OR)", "context": 200000, "vision": True, "cost_1m_in": 3.0, "cost_1m_out": 15.0},
            "openai/gpt-4o": {"name": "GPT-4o (OR)", "context": 128000, "vision": True, "cost_1m_in": 2.5, "cost_1m_out": 10.0},
            "google/gemini-2.5-pro": {"name": "Gemini Pro (OR)", "context": 1000000, "vision": True, "cost_1m_in": 1.25, "cost_1m_out": 10.0},
            "meta-llama/llama-3.3-70b": {"name": "Llama 3.3 70B (OR)", "context": 128000, "vision": False, "cost_1m_in": 0.59, "cost_1m_out": 0.79},
        },
        "default": "meta-llama/llama-3.3-70b",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "base_url": "http://localhost:11434/api/chat",
        "env_key": "OLLAMA_BASE_URL",
        "format": "ollama",
        "models": {
            "llama3.1": {"name": "Llama 3.1 (Local)", "context": 128000, "vision": False, "cost_1m_in": 0.0, "cost_1m_out": 0.0},
            "mistral": {"name": "Mistral (Local)", "context": 32000, "vision": False, "cost_1m_in": 0.0, "cost_1m_out": 0.0},
            "qwen2.5": {"name": "Qwen 2.5 (Local)", "context": 128000, "vision": False, "cost_1m_in": 0.0, "cost_1m_out": 0.0},
            "deepseek-r1:8b": {"name": "DeepSeek R1 8B (Local)", "context": 64000, "vision": False, "cost_1m_in": 0.0, "cost_1m_out": 0.0},
        },
        "default": "llama3.1",
    },
}


# ─── ROUTER ───────────────────────────────────────────────

class ModelRouter:
    """Routes LLM calls to the correct provider with the correct format."""

    def __init__(self):
        self._api_keys: dict[str, str] = {}
        self._load_keys()

    def _load_keys(self):
        """Load API keys from environment."""
        for provider_id, config in PROVIDERS.items():
            key = os.getenv(config["env_key"], "")
            if key:
                self._api_keys[provider_id] = key
                # For Ollama, the key is actually the base URL
                if provider_id == "ollama" and key.startswith("http"):
                    PROVIDERS["ollama"]["base_url"] = key.rstrip("/") + "/api/chat"

    def get_available_providers(self) -> list[dict]:
        """List providers that have API keys configured."""
        available = []
        for pid, config in PROVIDERS.items():
            has_key = pid in self._api_keys
            # Ollama is always "available" if URL is set
            if pid == "ollama":
                has_key = bool(os.getenv("OLLAMA_BASE_URL", ""))
            available.append({
                "provider": pid,
                "name": config["name"],
                "available": has_key,
                "models": [
                    {"model_id": mid, "name": minfo["name"], "vision": minfo["vision"],
                     "context_window": minfo["context"], "cost_per_1m_input": minfo["cost_1m_in"],
                     "cost_per_1m_output": minfo["cost_1m_out"]}
                    for mid, minfo in config["models"].items()
                ],
                "default_model": config["default"],
            })
        return available

    def resolve_model(self, model_id: str = None) -> tuple[str, str, dict]:
        """Resolve a model ID to (provider_id, model_id, provider_config).
        Accepts formats: 'provider/model', 'model', or None for default."""
        if not model_id:
            # Default: best available
            for preferred in ["anthropic", "openai", "groq", "deepseek", "google", "openrouter", "ollama"]:
                if preferred in self._api_keys:
                    config = PROVIDERS[preferred]
                    return preferred, config["default"], config
            # Nothing configured, try anthropic anyway
            config = PROVIDERS["anthropic"]
            return "anthropic", config["default"], config

        # Check if format is provider/model
        if "/" in model_id:
            parts = model_id.split("/", 1)
            # Check if it's an OpenRouter style model (e.g. "anthropic/claude-sonnet-4")
            for pid, config in PROVIDERS.items():
                if model_id in config["models"]:
                    return pid, model_id, config
            # Try as provider/model
            provider_id = parts[0]
            model_name = parts[1]
            if provider_id in PROVIDERS:
                config = PROVIDERS[provider_id]
                if model_name in config["models"]:
                    return provider_id, model_name, config
                # Model not found but provider is — use default
                return provider_id, config["default"], config

        # Search all providers for this model
        for pid, config in PROVIDERS.items():
            if model_id in config["models"]:
                return pid, model_id, config

        # Not found — fall back to default
        logger.warning(f"Unknown model '{model_id}', falling back to default")
        return self.resolve_model(None)

    def get_api_key(self, provider_id: str) -> str:
        return self._api_keys.get(provider_id, os.getenv(PROVIDERS.get(provider_id, {}).get("env_key", ""), ""))

    async def call(
        self,
        model_id: str = None,
        system_prompt: str = "",
        messages: list[dict] = None,
        tools: list[dict] = None,
        max_tokens: int = 2048,
        image_data: str = None,
        image_media_type: str = "image/jpeg",
    ) -> dict:
        """Universal LLM call — routes to correct provider, returns normalized response."""
        provider_id, resolved_model, config = self.resolve_model(model_id)
        api_key = self.get_api_key(provider_id)
        fmt = config["format"]

        if fmt == "anthropic":
            return await self._call_anthropic(api_key, resolved_model, system_prompt, messages, tools, max_tokens, image_data, image_media_type)
        elif fmt == "openai":
            return await self._call_openai(config["base_url"], api_key, resolved_model, system_prompt, messages, tools, max_tokens, provider_id, image_data, image_media_type)
        elif fmt == "google":
            return await self._call_google(api_key, resolved_model, system_prompt, messages, max_tokens, image_data, image_media_type)
        elif fmt == "ollama":
            return await self._call_ollama(config["base_url"], resolved_model, system_prompt, messages, max_tokens)
        else:
            return {"error": f"Unknown format: {fmt}", "text": "", "tool_calls": [], "usage": {}}

    async def _call_anthropic(self, api_key, model, system_prompt, messages, tools, max_tokens, image_data=None, image_media_type="image/jpeg"):
        """Call Anthropic Claude API."""
        # Build messages with vision support
        api_messages = []
        for msg in (messages or []):
            if msg["role"] == "user" and image_data and msg == messages[0]:
                # Inject image into first user message
                content = [
                    {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_data}},
                    {"type": "text", "text": msg["content"] if isinstance(msg["content"], str) else str(msg["content"])},
                ]
                api_messages.append({"role": "user", "content": content})
            else:
                api_messages.append(msg)

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": api_messages or [{"role": "user", "content": ""}],
        }
        if tools:
            payload["tools"] = tools

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=payload,
            )

        if resp.status_code != 200:
            return {"error": f"Anthropic API {resp.status_code}: {resp.text[:200]}", "text": "", "tool_calls": [], "usage": {}}

        data = resp.json()
        text_parts = []
        tool_calls = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append(block)

        usage = data.get("usage", {})
        return {
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "stop_reason": data.get("stop_reason", ""),
            "raw_content": data.get("content", []),
            "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)},
            "model": model,
            "provider": "anthropic",
        }

    async def _call_openai(self, base_url, api_key, model, system_prompt, messages, tools, max_tokens, provider_id="openai", image_data=None, image_media_type="image/jpeg"):
        """Call OpenAI-compatible API (GPT, Groq, DeepSeek, Mistral, xAI, OpenRouter)."""
        oai_messages = []
        if system_prompt:
            oai_messages.append({"role": "system", "content": system_prompt})

        for msg in (messages or []):
            if isinstance(msg.get("content"), str):
                if msg["role"] == "user" and image_data and msg == (messages or [])[0]:
                    oai_messages.append({"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{image_media_type};base64,{image_data}"}},
                        {"type": "text", "text": msg["content"]},
                    ]})
                else:
                    oai_messages.append(msg)
            elif isinstance(msg.get("content"), list):
                # Handle tool results — convert from Anthropic format
                converted = []
                for item in msg["content"]:
                    if item.get("type") == "tool_result":
                        converted.append({"role": "tool", "tool_call_id": item.get("tool_use_id", ""), "content": str(item.get("content", ""))})
                if converted:
                    oai_messages.extend(converted)
                else:
                    oai_messages.append(msg)
            else:
                oai_messages.append(msg)

        payload = {"model": model, "messages": oai_messages, "max_tokens": max_tokens}

        # Convert Anthropic tool format to OpenAI function format
        if tools:
            oai_tools = []
            for t in tools:
                oai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                })
            payload["tools"] = oai_tools

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider_id == "openrouter":
            headers["HTTP-Referer"] = "https://apex-swarm.com"
            headers["X-Title"] = "APEX SWARM"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(base_url, headers=headers, json=payload)

        if resp.status_code != 200:
            return {"error": f"{provider_id} API {resp.status_code}: {resp.text[:200]}", "text": "", "tool_calls": [], "usage": {}}

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})

        # Convert OpenAI tool_calls to Anthropic format for compatibility
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except Exception:
                    args = {}
                tool_calls.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })

        # Build raw_content in Anthropic format for execute_with_tools compatibility
        raw_content = []
        if msg.get("content"):
            raw_content.append({"type": "text", "text": msg["content"]})
        for tc in tool_calls:
            raw_content.append(tc)

        usage = data.get("usage", {})
        stop_reason = "tool_use" if tool_calls else "end_turn"

        return {
            "text": msg.get("content", "") or "",
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "raw_content": raw_content,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
            "model": model,
            "provider": provider_id,
        }

    async def _call_google(self, api_key, model, system_prompt, messages, max_tokens, image_data=None, image_media_type="image/jpeg"):
        """Call Google Gemini API."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"[System instructions]: {system_prompt}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will follow these instructions."}]})

        for msg in (messages or []):
            role = "user" if msg["role"] == "user" else "model"
            parts = []
            if role == "user" and image_data and msg == (messages or [])[0]:
                parts.append({"inline_data": {"mime_type": image_media_type, "data": image_data}})
            text = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            parts.append({"text": text})
            contents.append({"role": role, "parts": parts})

        payload = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)

        if resp.status_code != 200:
            return {"error": f"Google API {resp.status_code}: {resp.text[:200]}", "text": "", "tool_calls": [], "usage": {}}

        data = resp.json()
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts)

        usage_meta = data.get("usageMetadata", {})
        return {
            "text": text,
            "tool_calls": [],
            "stop_reason": "end_turn",
            "raw_content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": usage_meta.get("promptTokenCount", 0), "output_tokens": usage_meta.get("candidatesTokenCount", 0)},
            "model": model,
            "provider": "google",
        }

    async def _call_ollama(self, base_url, model, system_prompt, messages, max_tokens):
        """Call Ollama local API."""
        ollama_messages = []
        if system_prompt:
            ollama_messages.append({"role": "system", "content": system_prompt})
        for msg in (messages or []):
            content = msg["content"] if isinstance(msg["content"], str) else str(msg["content"])
            ollama_messages.append({"role": msg["role"], "content": content})

        payload = {"model": model, "messages": ollama_messages, "stream": False}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(base_url, json=payload)

        if resp.status_code != 200:
            return {"error": f"Ollama {resp.status_code}: {resp.text[:200]}", "text": "", "tool_calls": [], "usage": {}}

        data = resp.json()
        text = data.get("message", {}).get("content", "")
        return {
            "text": text,
            "tool_calls": [],
            "stop_reason": "end_turn",
            "raw_content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": data.get("prompt_eval_count", 0), "output_tokens": data.get("eval_count", 0)},
            "model": model,
            "provider": "ollama",
        }


# ─── GLOBAL INSTANCE ─────────────────────────────────────

model_router = ModelRouter()
