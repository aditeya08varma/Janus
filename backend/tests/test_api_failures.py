from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

import api as app_module


def test_missing_session_id():
    with patch("api.graph_builder") as mock_builder:
        mock_builder.compile.return_value = MagicMock()
        with TestClient(app_module.app) as client:
            response = client.post("/chat", json={"message": "Hello?"})
            assert response.status_code == 422
            assert "session_id" in response.text


@patch("api.graph_builder")
def test_ai_brain_crash(mock_graph_builder):
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
    mock_graph.astream.side_effect = Exception("Simulated graph failure")
    mock_graph_builder.compile.return_value = mock_graph

    payload = {"message": "Test", "session_id": "test-crash-1"}
    with TestClient(app_module.app) as client:
        with client.stream("POST", "/chat", json=payload) as response:
            assert response.status_code == 200
            full_text = "".join([line.decode("utf-8") for line in response.iter_bytes()])
            assert "[CRITICAL ERROR:" in full_text
            assert "Simulated graph failure" not in full_text


@patch("api.graph_builder")
def test_startup_uses_compiled_graph_once(mock_graph_builder):
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))

    async def empty_stream(*args, **kwargs):
        if False:
            yield None

    mock_graph.astream.side_effect = empty_stream
    mock_graph_builder.compile.return_value = mock_graph

    with TestClient(app_module.app) as client:
        client.post("/chat", json={"message": "one", "session_id": "s1"})
        client.post("/chat", json={"message": "two", "session_id": "s1"})
        assert mock_graph_builder.compile.call_count == 1


@patch("api.graph_builder")
def test_history_endpoint(mock_graph_builder):
    human = MagicMock(type="human", content="What is min weight?", tool_calls=[])
    ai = MagicMock(type="ai", content="770 kg", tool_calls=[])
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={"messages": [human, ai]}))
    mock_graph_builder.compile.return_value = mock_graph

    with TestClient(app_module.app) as client:
        response = client.get("/history/sess-1")
        assert response.status_code == 200
        body = response.json()
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][1]["role"] == "bot"
        assert "770 kg" in body["messages"][1]["text"]


def test_api_key_required_when_configured(monkeypatch):
    monkeypatch.setenv("JANUS_API_KEY", "secret-key")
    with patch("api.graph_builder") as mock_builder:
        mock_builder.compile.return_value = MagicMock()
        with TestClient(app_module.app) as client:
            denied = client.get("/health")
            assert denied.status_code == 200
            blocked = client.get("/history/x")
            assert blocked.status_code == 401
            ok = client.get("/history/x", headers={"X-API-Key": "secret-key"})
            assert ok.status_code in {200, 500}
