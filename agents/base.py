"""
Base agent class wrapping LiteLLM with config-driven model selection.
Supports async chat completions, JSON mode, and structured logging.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import structlog
import litellm
from litellm import acompletion

# Configure litellm
litellm.set_verbose = False

logger = structlog.get_logger(__name__)


class BaseAgent:
    """
    Base class for all agents in the job-apply pipeline.

    Wraps LiteLLM to provide:
    - Config-driven model selection by task name
    - Async chat completions
    - JSON-mode / structured output support
    - Retry logic
    - Structured logging
    """

    def __init__(self, config: dict, agent_name: str):
        """
        Args:
            config: Full config dict loaded from config.yaml + env
            agent_name: Human-readable name for this agent (used in logs)
        """
        self.config = config
        self.agent_name = agent_name
        self.llm_config = config.get("llm", {})
        self.models = self.llm_config.get("models", {})

        self.log = structlog.get_logger(agent_name)
        self.log.info("agent_initialized", agent=agent_name)

    def _resolve_model(self, task: str) -> str:
        """
        Resolve the LiteLLM model string for a given task.
        Each model string in config.yaml carries its own provider prefix:
          openrouter/anthropic/claude-sonnet-4-5  -> OpenRouter
          openrouter/openai/gpt-4o-mini           -> OpenRouter
          ollama/llama3.1:8b                      -> local Ollama
        """
        model = self.models.get(task)
        if not model:
            model = self.models.get("orchestrator", "openrouter/openai/gpt-4o-mini")
        return model

    def _build_api_params(self, model: str) -> dict:
        """
        Build provider-specific API params from the model's prefix.
        LiteLLM routes automatically based on prefix; we just supply credentials.
        """
        params = {}

        if model.startswith("openrouter/"):
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if api_key:
                params["api_key"] = api_key
            params["api_base"] = "https://openrouter.ai/api/v1"

        elif model.startswith("gemini/"):
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if api_key:
                params["api_key"] = api_key

        elif model.startswith("ollama/"):
            params["api_base"] = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

        return params

    async def chat(
        self,
        task: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        json_mode: bool = False,
        extra_params: Optional[dict] = None,
    ) -> str:
        """
        Send a chat completion request via LiteLLM.

        Args:
            task: Task name used to look up the correct model from config
            messages: OpenAI-format message list
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            json_mode: If True, request JSON output (response_format)
            extra_params: Additional kwargs to pass to litellm.acompletion

        Returns:
            The assistant message content as a string.
        """
        model = self._resolve_model(task)
        api_params = self._build_api_params(model)

        if extra_params:
            api_params.update(extra_params)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **api_params,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        self.log.info(
            "llm_request",
            task=task,
            model=model,
            message_count=len(messages),
            json_mode=json_mode,
        )

        start = time.monotonic()
        try:
            response = await acompletion(**kwargs)
            elapsed = time.monotonic() - start
            content = response.choices[0].message.content or ""

            usage = response.usage
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            self.log.info(
                "llm_response",
                task=task,
                model=model,
                elapsed_s=round(elapsed, 2),
                output_tokens=getattr(usage, "completion_tokens", None),
                cache_read_tokens=getattr(prompt_details, "cached_tokens", None),
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            )
            return content

        except Exception as e:
            elapsed = time.monotonic() - start
            self.log.error(
                "llm_error",
                task=task,
                model=model,
                elapsed_s=round(elapsed, 2),
                error=str(e),
            )
            raise

    async def chat_json(
        self,
        task: str,
        messages: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict:
        """
        Like chat(), but parses and returns the JSON object from the response.

        Raises:
            json.JSONDecodeError: If the model response is not valid JSON.
        """
        raw = await self.chat(
            task=task,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            match = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
            if match:
                return json.loads(match.group(1).strip())
            raise

    def build_system_message(self, content: str) -> dict:
        """Helper to build a system message dict."""
        return {"role": "system", "content": content}

    def build_user_message(self, content: str) -> dict:
        """Helper to build a user message dict."""
        return {"role": "user", "content": content}

    def build_assistant_message(self, content: str) -> dict:
        """Helper to build an assistant message dict."""
        return {"role": "assistant", "content": content}
