"""Local language model through Ollama (Phase 8, ADR-020).

Talks only to an Ollama server on this computer (a loopback address), so
reports never leave the machine and nothing is paid for. Asks for JSON in
the shape ``alpha.explain`` validates. Low temperature and a fixed seed
keep answers repeatable.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from alpha.explain import RewriteError

DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TIMEOUT_S = 180.0
MAX_ANSWER_BYTES = 256 * 1024

# Structured output: Ollama constrains the answer to this JSON schema.
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "facts"],
            },
        }
    },
    "required": ["statements"],
}


class _GpuError(Exception):
    """Ollama's GPU backend failed (driver or CUDA problem)."""


class OllamaRewriter:
    """``(system, user) -> JSON text`` for ``alpha.explain.explain``."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_URL,
        timeout_s: float = TIMEOUT_S,
        cpu_only: bool = False,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or parts.hostname not in LOOPBACK:
            raise ValueError(
                "only a local Ollama server is allowed (http://127.0.0.1:...); "
                "reports must not leave this computer"
            )
        self.model = model
        self.url = base_url.rstrip("/") + "/api/chat"
        self.timeout_s = timeout_s
        self.cpu_only = cpu_only
        self.notes: list[str] = []  # e.g. that the GPU failed and the CPU was used

    def __call__(self, system: str, user: str) -> str:
        try:
            return self._chat(system, user)
        except _GpuError as exc:
            # A GPU/driver problem in Ollama, not in the request: the same
            # model runs on the CPU, only slower.
            self.cpu_only = True
            self.notes.append(
                f"Ollama's GPU backend failed ({exc}); ran on the CPU instead. "
                "Updating the graphics driver usually fixes this."
            )
            return self._chat(system, user)

    def _chat(self, system: str, user: str) -> str:
        options: dict[str, float | int] = {"temperature": 0.2, "seed": 7}
        if self.cpu_only:
            options["num_gpu"] = 0
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": ANSWER_SCHEMA,
                "options": options,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read(MAX_ANSWER_BYTES + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace")
            if exc.code == 404:
                raise RewriteError(
                    f"model '{self.model}' is not installed (ollama pull {self.model})"
                ) from None
            if "CUDA" in detail and not self.cpu_only:
                raise _GpuError("CUDA error") from None
            raise RewriteError(f"Ollama returned HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise RewriteError(
                f"no local Ollama server at {self.url} ({reason}); start Ollama"
            ) from None
        if len(raw) > MAX_ANSWER_BYTES:
            raise RewriteError("the model's answer is unexpectedly long")
        try:
            content = json.loads(raw)["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RewriteError(f"unexpected answer from Ollama: {exc}") from None
        if not isinstance(content, str) or not content.strip():
            raise RewriteError("the model answered nothing")
        return content
