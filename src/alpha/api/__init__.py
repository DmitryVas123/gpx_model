"""HTTP API and minimal UI. The only package that imports FastAPI and Pydantic."""

from alpha.api.app import create_app

__all__ = ["create_app"]
