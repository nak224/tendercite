import json
import math
from typing import TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


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
    ) -> None:
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be finite and between 0 and 2")
        if max_output_tokens is not None and (
            type(max_output_tokens) is not int or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("request_timeout must be finite and positive")
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.request_timeout = request_timeout

    def build_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> dict:
        """Build the exact generation payload without HTTP or credentials, for preflight."""
        schema = response_model.model_json_schema()
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
            payload["max_tokens"] = self.max_output_tokens
        return payload

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = self.build_payload(
            system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model
        )
        async with httpx.AsyncClient(
            timeout=self.request_timeout, transport=self.transport, follow_redirects=False
        ) as client:
            async with client.stream(
                "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise ValueError("Provider response exceeds supported size")
        result = json.loads(body)
        choice = result["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise ValueError("Provider output was truncated or refused")
        content = choice["message"]["content"]
        return response_model.model_validate_json(content)
