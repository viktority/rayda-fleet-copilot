"""Small provider boundary; scripted models use the same graph in offline evaluations."""

import os
from dataclasses import dataclass
from typing import Protocol

from openai import APIError, OpenAI

from .domain import FleetError


@dataclass
class ModelTurn:
    items: list[dict]
    calls: list[dict]
    usage: dict


class Model(Protocol):
    name: str

    def next(
        self, instructions: str, messages: list[dict], tools: list[dict], planning: bool
    ) -> ModelTurn: ...


class OpenAIModel:
    def __init__(self, model: str | None = None):
        if not os.getenv("OPENAI_API_KEY"):
            raise FleetError("missing_credentials", "OPENAI_API_KEY is not configured", 503)
        self.name = model or os.getenv("OPENAI_MODEL", "gpt-6.2-sol")
        self.client = OpenAI(timeout=60.0, max_retries=1)

    def next(self, instructions, messages, tools, planning):
        try:
            response = self.client.responses.create(
                model=self.name,
                instructions=instructions,
                input=messages,
                tools=tools,
                tool_choice={"type": "function", "name": "make_plan"} if planning else "required",
                parallel_tool_calls=False,
                store=False,
                max_output_tokens=2500,
                reasoning={"effort": "low"},
                include=["reasoning.encrypted_content"],
            )
        except APIError as exc:
            # Provider exception text may contain request details. Return only safe status metadata.
            status = getattr(exc, "status_code", None)
            code = getattr(exc, "code", None)
            safe_codes = {"insufficient_quota", "invalid_api_key", "model_not_found", "rate_limit_exceeded"}
            label = code if code in safe_codes else "provider_error"
            raise FleetError(
                label, f"Model request failed ({label}; HTTP {status or 'unavailable'})", 503
            ) from None
        if response.status != "completed":
            raise FleetError(
                "incomplete_model_response", "Model response did not complete within its output budget", 503
            )
        items = [item.model_dump(exclude_none=True) for item in response.output]
        calls = [i for i in items if i["type"] == "function_call"]
        return ModelTurn(
            items=items, calls=calls, usage=response.usage.model_dump() if response.usage else {}
        )
