import logging

from log_setup import LOGGER_NAME, configure_logging


def test_returns_named_logger_not_root():
    logger = configure_logging(debug=False)
    assert logger.name == LOGGER_NAME
    assert logger is not logging.getLogger()


def test_does_not_configure_root_logger():
    root_handlers_before = list(logging.getLogger().handlers)
    configure_logging(debug=False)
    root_handlers_after = list(logging.getLogger().handlers)
    assert root_handlers_after == root_handlers_before


def test_debug_flag_sets_debug_level():
    logger = configure_logging(debug=True)
    assert logger.level == logging.DEBUG


def test_default_is_info_level():
    logger = configure_logging(debug=False)
    assert logger.level == logging.INFO


def test_handlers_are_stream_handlers_only():
    logger = configure_logging(debug=False)
    assert logger.handlers, "expected at least one handler"
    for h in logger.handlers:
        assert isinstance(h, logging.StreamHandler)
        assert not isinstance(h, logging.FileHandler)


def test_does_not_propagate_to_root():
    logger = configure_logging(debug=False)
    assert logger.propagate is False
