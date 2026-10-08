from pathlib import Path

import httpx
import pytest


def test_ui_sections_render_against_real_api(client, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    def request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        return client.request(method, url.replace("http://localhost:8000", ""), **kwargs)

    monkeypatch.setattr(httpx, "request", request)
    ui = AppTest.from_file(str(Path(__file__).parents[1] / "frontend" / "app.py")).run()
    assert not ui.exception
    assert ui.title[0].value == "TenderCite"
    for section in ["Search", "Analysis & review", "Go / No-Go", "Export"]:
        ui.sidebar.radio[0].set_value(section).run()
        assert not ui.exception
    assert len(ui.get("download_button")) == 3


def test_ui_prominently_marks_unsupported_finding(client, retrieval, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from test_analysis import create_run

    create_run(client, "wrong_quote")

    def request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        return client.request(method, url.replace("http://localhost:8000", ""), **kwargs)

    monkeypatch.setattr(httpx, "request", request)
    ui = AppTest.from_file(str(Path(__file__).parents[1] / "frontend" / "app.py")).run()
    ui.sidebar.radio[0].set_value("Analysis & review").run()
    assert not ui.exception
    assert any("INVALID_QUOTE" in error.value for error in ui.error)
    button = next(b for b in ui.button if b.label == "Save review")
    button.click().run()
    assert not ui.exception
    assert client.get("/api/v1/findings").json()[0]["review_status"] == "CONFIRMED"
