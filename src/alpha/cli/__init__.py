"""Command-line interface (entry point ``alpha``)."""

from alpha.cli.app import (
    EXIT_BAD_PARAMS,
    EXIT_INVALID,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_READ_ERROR,
    EXIT_UNAVAILABLE,
    main,
)

__all__ = [
    "EXIT_BAD_PARAMS",
    "EXIT_INVALID",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_READ_ERROR",
    "EXIT_UNAVAILABLE",
    "main",
]
