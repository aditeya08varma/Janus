from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

import api as app_module


def _fake_stream(*args, **kwargs):
    async def gen():
        fake_message = MagicMock()
        fake_message.content = "System Operational"
        fake_message.tool_calls = []
        yield {"agent": {"messages": [fake_message]}}

    return gen()


@patch("api.graph_builder")
def test_chat_endpoint_structure(mock_graph_builder):
    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(values={}))
    mock_graph.astream.side_effect = lambda *a, **k: _fake_stream()
    mock_graph_builder.compile.return_value = mock_graph

    with TestClient(app_module.app) as client:
        payload = {"message": "Status Report", "session_id": "test-session-001"}
        with client.stream("POST", "/chat", json=payload) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert "text/plain" in response.headers.get("content-type", "")
            full_response_text = "".join(line.decode("utf-8") for line in response.iter_bytes())
            assert "System Operational" in full_response_text
