import asyncio
import json
from io import BytesIO

import httpx
import pytest
from conftest import make_pdf
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from tendercite.api.dependencies import get_repository
from tendercite.core.config import Settings, settings
from tendercite.domain.analysis import ExtractionResult
from tendercite.main import app
from tendercite.services.llm.openai_compatible import OpenAICompatibleLLM


@pytest.mark.parametrize(
    "name,payload,mime,status",
    [
        ("tender.txt", b"hello", "text/plain", 415),
        ("tender.pdf", b"hello", "application/pdf", 415),
        ("tender.pdf", b"%PDF-1.7\nbroken", "application/pdf", 422),
        ("tender.pdf", b"%PDF-1.7\nbroken", "image/png", 415),
    ],
)
def test_invalid_uploads_leave_no_files(client, name, payload, mime, status):
    result = client.post("/api/v1/documents", files={"file": (name, payload, mime)})
    assert result.status_code == status
    assert client.get("/api/v1/documents").json() == []
    assert not list(settings.upload_dir.glob("*.pdf"))
    assert "/workspace" not in result.text


def test_upload_limits_and_encryption(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("big.pdf", b"%PDF-" + b"x" * (1024 * 1024), "application/pdf")},
    )
    assert response.status_code == 413
    monkeypatch.setattr(settings, "max_pdf_pages", 1)
    assert (
        client.post(
            "/api/v1/documents",
            files={"file": ("pages.pdf", make_pdf(["one", "two"]), "application/pdf")},
        ).status_code
        == 422
    )
    monkeypatch.setattr(settings, "max_extracted_chars", 3)
    assert (
        client.post(
            "/api/v1/documents",
            files={"file": ("text.pdf", make_pdf(["long text"]), "application/pdf")},
        ).status_code
        == 422
    )
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(make_pdf(["text"]))))
    writer.encrypt("test-only-password")
    buffer = BytesIO()
    writer.write(buffer)
    assert (
        client.post(
            "/api/v1/documents",
            files={"file": ("encrypted.pdf", buffer.getvalue(), "application/pdf")},
        ).status_code
        == 422
    )
    assert not list(settings.upload_dir.glob("*.pdf"))


@pytest.mark.parametrize("name", ["../../tender.pdf", r"C:\private\tender.pdf"])
def test_paths_cannot_escape_upload_directory(client, name):
    result = client.post(
        "/api/v1/documents", files={"file": (name, make_pdf(["source"]), "application/pdf")}
    )
    assert result.status_code == 201
    assert result.json()["filename"] == "tender.pdf"
    assert (settings.upload_dir / (result.json()["id"] + ".pdf")).exists()


def test_storage_failure_cleans_up_file(client, monkeypatch):
    repo = app.dependency_overrides[get_repository]()

    def fail(**kwargs):
        raise RuntimeError("sensitive internal storage detail")

    monkeypatch.setattr(repo, "save_document", fail)
    result = client.post(
        "/api/v1/documents", files={"file": ("test.pdf", make_pdf(["text"]), "application/pdf")}
    )
    assert result.status_code == 500
    assert "sensitive" not in result.text
    assert not list(settings.upload_dir.glob("*.pdf"))


def test_provider_configuration_hides_secret(client, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "llm_api_key", SecretStr("test-only-secret"))
    assert "test-only-secret" not in client.get("/api/v1/configuration").text
    for url in [
        "file:///etc/passwd",
        "https://user:password@example.com/v1",
        "https://example.com/v1?key=value",
    ]:
        with pytest.raises(ValidationError):
            Settings(llm_base_url=url, _env_file=None)


def test_openai_compatible_wire_contract_and_schema_validation():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["model"] == "test-model"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"findings": []}'}}]
            },
        )

    llm = OpenAICompatibleLLM(
        base_url="https://provider.invalid/v1",
        model="test-model",
        api_key="test-only-key",
        transport=httpx.MockTransport(handler),
    )
    output = asyncio.run(
        llm.generate_structured(
            system_prompt="policy", user_prompt="data", response_model=ExtractionResult
        )
    )
    assert output.findings == []
    for response in [
        httpx.Response(200, content=b"x" * 2_000_001),
        httpx.Response(200, json={"choices": [{"finish_reason": "length"}]}),
    ]:
        llm.transport = httpx.MockTransport(lambda request, response=response: response)
        with pytest.raises(ValueError):
            asyncio.run(
                llm.generate_structured(
                    system_prompt="policy", user_prompt="data", response_model=ExtractionResult
                )
            )
