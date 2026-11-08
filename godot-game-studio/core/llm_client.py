"""Thin Anthropic SDK wrapper: retries, JSON extraction, schema validation."""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from core.config import LLMConfig

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_RETRYABLE_STATUS = {408, 409, 429}


class LLMError(Exception):
    """Raised when the LLM call or JSON validation ultimately fails."""


def extract_json_text(text: str) -> str:
    """Return the most likely JSON object substring from a model reply."""
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model reply")
    return text[start : end + 1]


class LLMClient:
    """Calls the Anthropic Messages API. Model name comes from config (env var)."""

    def __init__(self, cfg: LLMConfig, client: anthropic.Anthropic | None = None) -> None:
        self._cfg = cfg
        self._client = client or anthropic.Anthropic(api_key=cfg.api_key)
        # Some models reject `temperature`; we drop it automatically if so.
        self._send_temperature = cfg.temperature is not None
        self._local = threading.local()  # last stop_reason per thread

    @property
    def last_stop_reason(self) -> str | None:
        """Stop reason of the last successful call made from the current thread."""
        return getattr(self._local, "stop_reason", None)

    # ------------------------------------------------------------------
    def _call(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int | None,
    ) -> str:
        """Single API call with exponential-backoff retries. Returns reply text."""
        last_error: Exception | None = None
        for attempt in range(self._cfg.max_retries + 1):
            kwargs: dict[str, object] = {
                "model": self._cfg.model,
                "max_tokens": max_tokens or self._cfg.max_tokens,
                "system": system,
                "messages": messages,
            }
            if self._send_temperature and self._cfg.temperature is not None:
                kwargs["temperature"] = self._cfg.temperature
            try:
                response = self._client.messages.create(**kwargs)  # type: ignore[arg-type]
            except anthropic.BadRequestError as exc:
                if self._send_temperature and "temperature" in str(exc).lower():
                    logger.warning("Model rejected 'temperature'; retrying without it.")
                    self._send_temperature = False
                    last_error = exc
                    continue
                raise LLMError(f"Bad request to the API: {exc}") from exc
            except anthropic.APIConnectionError as exc:
                last_error = exc
            except anthropic.APIStatusError as exc:
                if exc.status_code in _RETRYABLE_STATUS or exc.status_code >= 500:
                    last_error = exc
                else:
                    raise LLMError(f"API error {exc.status_code}: {exc}") from exc
            else:
                self._local.stop_reason = response.stop_reason
                if response.stop_reason == "max_tokens":
                    logger.warning("Reply hit max_tokens; output may be truncated.")
                return "".join(
                    block.text for block in response.content if getattr(block, "type", "") == "text"
                )

            delay = min(2.0**attempt + random.random(), 30.0)
            logger.warning(
                "LLM call failed (%s); retry %d/%d in %.1fs",
                type(last_error).__name__,
                attempt + 1,
                self._cfg.max_retries,
                delay,
            )
            time.sleep(delay)
        raise LLMError(f"LLM call failed after retries: {last_error}")

    # ------------------------------------------------------------------
    def complete(self, system: str, user: str, max_tokens: int | None = None) -> str:
        """Plain-text completion."""
        return self._call(system, [{"role": "user", "content": user}], max_tokens)

    def complete_json(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        max_attempts: int = 3,
        max_tokens: int | None = None,
    ) -> T:
        """Completion that must return a JSON object validated against `schema`.

        On invalid output, the validation error is sent back to the model and
        it is asked to resend, up to `max_attempts` times.
        """
        json_system = (
            f"{system}\n\n"
            "OUTPUT FORMAT: reply with ONE JSON object only, no prose and no Markdown fences, "
            "matching this JSON Schema exactly:\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": user}]
        last_problem = "unknown"
        for attempt in range(1, max_attempts + 1):
            reply = self._call(json_system, messages, max_tokens)
            try:
                return schema.model_validate_json(extract_json_text(reply))
            except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
                last_problem = str(exc)[:1500]
                logger.warning(
                    "Invalid JSON for %s (attempt %d/%d): %s",
                    schema.__name__,
                    attempt,
                    max_attempts,
                    last_problem.splitlines()[0] if last_problem else "",
                )
                messages.append({"role": "assistant", "content": reply or "(empty)"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your reply was not valid for the required schema:\n"
                            f"{last_problem}\n"
                            "Resend the COMPLETE corrected JSON object only."
                        ),
                    }
                )
        raise LLMError(f"No valid {schema.__name__} after {max_attempts} attempts: {last_problem}")
