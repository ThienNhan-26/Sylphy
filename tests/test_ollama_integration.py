import os
from pathlib import Path
import tempfile

import pytest

from sylphy.context import ContextBuilder
from sylphy.core import ConversationService
from sylphy.ollama_provider import OllamaProvider
from sylphy.storage import SQLiteStore
from sylphy.runtime import Runtime


@pytest.mark.integration
def test_local_ollama_answers_when_available():
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    model = os.getenv("SYLPHY_TEST_MODEL", "spark-t1000:latest")
    provider = OllamaProvider(host, model, timeout_seconds=180)
    test_db = Path(os.getenv("SYLPHY_TEST_DB", Path(tempfile.gettempdir()) / "sylphy-integration.sqlite3"))
    store = SQLiteStore(test_db)
    session = store.create_session("integration")
    service = ConversationService(provider, store, ContextBuilder("Reply exactly with requested marker.", "neutral identity", 512))
    runtime = Runtime(model, heartbeat_enabled=False)
    runtime.start()
    try:
        turn = runtime.run_conversation(service, session.id, "Reply with exactly SYLPHY_INTEGRATION_OK")
    finally:
        runtime.shutdown()
    assert "SYLPHY_INTEGRATION_OK" in turn.assistant.content
