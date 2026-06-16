from __future__ import annotations

import io
import json

from backend.utils.logger import PromptForgeLogger


def test_logger_emits_structured_json() -> None:
    stream = io.StringIO()
    logger = PromptForgeLogger("promptforge.test", stream=stream)

    logger.info("workflow started", execution_id="exec-1")

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["logger"] == "promptforge.test"
    assert payload["message"] == "workflow started"
    assert payload["context"] == {"execution_id": "exec-1"}


def test_stage_logging_sets_category() -> None:
    stream = io.StringIO()
    logger = PromptForgeLogger("promptforge.test", stream=stream)

    logger.flash("upload complete", port="COM4")

    payload = json.loads(stream.getvalue())
    assert payload["category"] == "flash"
    assert payload["context"] == {"port": "COM4"}
