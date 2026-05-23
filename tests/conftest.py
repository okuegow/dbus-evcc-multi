import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from log_setup import LOGGER_NAME


@pytest.fixture
def propagate_app_logger():
    """Let caplog capture from our named logger (which sets propagate=False
    in production to keep the root logger clean)."""
    logger = logging.getLogger(LOGGER_NAME)
    saved = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = saved
