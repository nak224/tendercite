import json
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

    def __init__(self, *, base_url: str, model: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key

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
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "tender_findings", "schema": schema},
            },
        }
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return response_model.model_validate_json(content)
