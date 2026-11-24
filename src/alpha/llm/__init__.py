"""Language-model adapter (Phase 8, ADR-020). Local only: Ollama on this
computer. Besides ``alpha.dem``, the only package allowed to open network
connections, and only to a loopback address."""

from alpha.llm.ollama import DEFAULT_MODEL, DEFAULT_URL, OllamaRewriter

__all__ = ["DEFAULT_MODEL", "DEFAULT_URL", "OllamaRewriter"]
