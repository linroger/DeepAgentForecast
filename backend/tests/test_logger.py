"""Regression checks for application logging lifecycle behavior."""

import io
import logging

from app.utils.logger import ClosedStreamSafeHandler


def test_closed_console_stream_does_not_emit_secondary_logging_error(capsys):
    stream = io.StringIO()
    handler = ClosedStreamSafeHandler(stream)
    logger = logging.Logger("closed-stream-regression", level=logging.INFO)
    logger.addHandler(handler)

    stream.close()
    logger.info("late atexit message")

    assert capsys.readouterr().err == ""
