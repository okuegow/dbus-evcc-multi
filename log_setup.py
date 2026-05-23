"""Named-logger setup per Victron mvader recommendation.

Critical: NEVER call logging.basicConfig() - that configures the root logger
and turns every dependency (urllib3, dbus, ...) verbose. Use a named logger
attached to a StreamHandler. stdout/stderr is captured by daemontools and
piped to multilog (see service/log/run).
"""
import logging
import sys

LOGGER_NAME = "dbus-evcc-multi"


def configure_logging(debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    level = logging.DEBUG if debug else logging.INFO

    if logger.handlers:
        logger.setLevel(level)
        return logger

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger
