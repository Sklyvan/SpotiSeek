"""Leveled logging configuration for the CLI."""

from __future__ import annotations

import logging

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def resolve_level(log_level: str | None, verbose: int) -> int:
    """Resolve the effective logging level.

    Explicit ``log_level`` wins; otherwise ``verbose`` bumps the default INFO
    down towards DEBUG (``-v`` -> DEBUG). ``-v``/``-vv`` are accepted as
    escalating shortcuts.
    """
    if log_level:
        return _LEVELS.get(log_level.upper(), logging.INFO)
    if verbose >= 1:
        return logging.DEBUG
    return logging.INFO


def configure_logging(log_level: str | None = None, verbose: int = 0) -> None:
    """Configure the root logger with a clean, level-aware formatter.

    Third-party libraries (aioslsk, spotipy, urllib3) are kept at WARNING
    unless we are in DEBUG mode, so their chatter does not drown out our own
    per-track progress lines.
    """
    level = resolve_level(log_level, verbose)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    # Reset handlers so repeated CLI invocations (and tests) do not stack them.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    noisy_level = logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    for name in ("aioslsk", "urllib3", "asyncio"):
        logging.getLogger(name).setLevel(noisy_level)

    quiet_level = logging.DEBUG if level <= logging.DEBUG else logging.CRITICAL
    # spotipy logs its own ERROR for the 403 premium gate that we already
    # catch, translate and re-log with a clearer message.
    # aioslsk.client dumps "unhandled exception on loop" tracebacks for the
    # many peers that are unreachable/behind NAT — this is normal Soulseek
    # churn, not something the user acted on. Real login/search/download
    # failures reach the user through SpotiSeek's own error handling.
    for name in ("spotipy", "aioslsk.client"):
        logging.getLogger(name).setLevel(quiet_level)
