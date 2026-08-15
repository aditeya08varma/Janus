import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

# Import your app
from api import app

client = TestClient(app)

# 1. We patch 'AsyncRedisSaver' so it doesn't try to connect to real Redis
# 2. We patch 'graph_builder' so we can intercept the .compile() call
@patch("api.AsyncRedisSaver")
@patch("api.graph_builder")
def test_chat_endpoint_structure(mock_graph_builder, mock_redis_saver):
    """
    Verifies that the /chat endpoint:
    1. Accepts a valid POST request
    2. Parses the JSON body correctly
    3. Returns a 200 OK status
    4. Streams back the text from the agent
    """
    
    # --- SETUP THE MOCKS ---

    # A. Mock the Redis Context Manager (async with AsyncRedisSaver...)
    # This ensures "async with ... as memory:" works without a real DB
    mock_memory_instance = MagicMock()
    mock_redis_manager = AsyncMock()
    mock_redis_manager.__aenter__.return_value = mock_memory_instance
    mock_redis_saver.from_conn_string.return_value = mock_redis_manager

    # B. Mock the Graph Compilation
    # When api.py calls graph_builder.compile(), return our fake graph
    mock_compiled_graph = MagicMock()
    mock_graph_builder.compile.return_value = mock_compiled_graph

    # C. Mock the Streaming Response (The "Agent")
    # This generator simulates the AI sending a message back
    async def fake_stream_generator(*args, **kwargs):
        # We simulate the structure LangGraph returns: a dict with "agent" -> "messages"
        fake_message = MagicMock()
        fake_message.content = "System Operational"
        fake_message.tool_calls = [] # No tools used in this test
        
        yield {"agent": {"messages": [fake_message]}}

    # Connect our fake generator to the .astream() method
    mock_compiled_graph.astream.side_effect = fake_stream_generator

    # --- EXECUTION ---

    payload = {
        "message": "Status Report",
        "session_id": "test-session-001"
    }
    
    # Send the request
    with client.stream("POST", "/chat", json=payload) as response:
        
        # ASSERTION 1: Did the server accept the request?
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        # ASSERTION 2: Did we get a stream?
        assert "text/plain" in response.headers.get("content-type", "")

        # ASSERTION 3: Did we get the right text back?
        # We iterate through the stream and join the text to check it
        full_response_text = "".join([line.decode("utf-8") for line in response.iter_bytes()])
        
        # Use 'in' because there might be __LOG__ lines or newlines
        assert "System Operational" in full_response_text

    print("\n✅ Smoke Test Passed: API connects, compiles graph, and streams response.")