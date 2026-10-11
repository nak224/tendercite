import json
import math
import time
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def strict_output_schema(schema: dict) -> dict:
    """Require nullable properties too, without changing other providers' schemas."""
    result = json.loads(json.dumps(schema))

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


@dataclass
class CompletionResponse:
    body: bytes
    http_status: int
    request_id: str | None
    latency_seconds: float


class ResponseLimitError(ValueError):
    def __init__(self, response: CompletionResponse):
        super().__init__("Provider response exceeds supported size")
        self.response = response


class OpenAICompatibleLLM:
    """Small provider adapter for OpenAI-compatible chat completion APIs.

    This deliberately avoids framework lock-in. It can target compatible hosted or local
    servers (for example Ollama/vLLM deployments exposing the same endpoint shape).
    """

    provider = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        temperature: float = 0,
        max_output_tokens: int | None = None,
        request_timeout: float = 90,
        reasoning_effort: str | None = None,
    ) -> None:
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be finite and between 0 and 2")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("request_timeout must be finite and positive")
        if reasoning_effort not in (None, "low"):
            raise ValueError("This pilot supports only explicit low reasoning effort")
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout = request_timeout
        self.reasoning_effort = reasoning_effort

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> dict:
        """Build the exact generation payload without HTTP or credentials, for preflight."""
        schema = response_model.model_json_schema()
        sol = self.model == "gpt-6.1-sol"
        if sol:
            schema = strict_output_schema(schema)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": user_prompt
                    + "\n\nReturn JSON matching this schema:\n"
                    + json.dumps(schema),
                },
            ],
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "tender_findings", "schema": schema},
            },
        }
        if self.max_output_tokens is not None:
            payload["max_completion_tokens" if sol else "max_tokens"] = self.max_output_tokens
        if sol:
            payload.pop("temperature")
            payload["reasoning_effort"] = self.reasoning_effort or "low"
            payload["response_format"]["json_schema"]["strict"] = True
        return payload

    async def request_completion(self, payload: dict) -> CompletionResponse:
        """One HTTP attempt, retaining response/usage for evaluation accounting."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.perf_counter()
        async with httpx.AsyncClient(
            timeout=self.request_timeout, transport=self.transport, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload
            ) as response:
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise ResponseLimitError(
                            CompletionResponse(
                                bytes(body[:2_000_000]),
                                response.status_code,
                                response.headers.get("x-request-id"),
                                time.perf_counter() - started,
                            )
                        )
                return CompletionResponse(
                    bytes(body),
                    response.status_code,
                    response.headers.get("x-request-id"),
                    time.perf_counter() - started,
                )

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        payload = self.build_payload(
            system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model
        )
        completion = await self.request_completion(payload)
        httpx.Response(
            completion.http_status,
            request=httpx.Request("POST", f"{self.base_url}/chat/completions"),
        ).raise_for_status()
        result = json.loads(completion.body)
        choice = result["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Provider output was truncated or refused")
        content = choice["message"]["content"]
        return response_model.model_validate_json(content)
