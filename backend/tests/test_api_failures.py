import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from api import app

# --- THIS WAS MISSING ---
client = TestClient(app)
# ------------------------

def test_missing_session_id():
    """
    Verifies that the API rejects requests missing the required 'session_id'.
    """
    # Payload missing 'session_id'
    bad_payload = {"message": "Hello?"}
    
    response = client.post("/chat", json=bad_payload)
    
    # Assert we get a 422 Validation Error
    assert response.status_code == 422
    
    # Check for "Field required" (Capital F)
    assert "Field required" in response.text
    assert "session_id" in response.text
    print("\n✅ Validation Test Passed: API correctly rejects bad inputs.")

@patch("api.AsyncRedisSaver")
@patch("api.graph_builder")
def test_ai_brain_crash(mock_graph_builder, mock_redis_saver):
    """
    Verifies that if the LangGraph execution fails, the API:
    1. Catches the exception
    2. Returns a 200 OK (the connection is still alive)
    3. Streams a [CRITICAL ERROR] log to the frontend
    """
    
    # --- SETUP MOCKS ---
    mock_memory_instance = MagicMock()
    mock_redis_manager = AsyncMock()
    mock_redis_manager.__aenter__.return_value = mock_memory_instance
    mock_redis_saver.from_conn_string.return_value = mock_redis_manager

    mock_compiled_graph = MagicMock()
    mock_graph_builder.compile.return_value = mock_compiled_graph

    # --- THE SABOTAGE ---
    mock_compiled_graph.astream.side_effect = Exception("Simulated Redis Failure")

    # --- EXECUTION ---
    payload = {"message": "Test", "session_id": "test-crash-1"}
    
    with client.stream("POST", "/chat", json=payload) as response:
        assert response.status_code == 200 
        
        full_text = "".join([line.decode("utf-8") for line in response.iter_bytes()])
        
        assert "[CRITICAL ERROR: Simulated Redis Failure]" in full_text

    print("✅ Resilience Test Passed: API handled the crash gracefully.")