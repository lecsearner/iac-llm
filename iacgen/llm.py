"""LLM client interface + an Anthropic Claude implementation and a mock.

The generator talks to an ``LLMClient`` abstraction so the provider can be
swapped (Claude today; Bedrock/OpenAI later) and so the plumbing can be tested
with ``MockLLM`` — no API key, no cost, no network.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


def _read_api_key() -> str | None:
    """Return the Anthropic key from the environment, or a local .env file.

    Resolution order: the ANTHROPIC_API_KEY environment variable first, then a
    ``.env`` file in the project root (KEY=VALUE lines). The ``.env`` file is
    git-ignored so the key never gets committed. The key is never logged.
    """
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env.strip()
    # Walk up from this file to find a .env (project root).
    for parent in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        dotenv = parent / ".env"
        if dotenv.is_file():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "ANTHROPIC_API_KEY":
                    return v.strip().strip('"').strip("'")
    return None


def _read_model() -> str | None:
    """Optional ANTHROPIC_MODEL override from env or .env (None if unset)."""
    env = os.environ.get("ANTHROPIC_MODEL")
    if env:
        return env.strip()
    for parent in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        dotenv = parent / ".env"
        if dotenv.is_file():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "ANTHROPIC_MODEL":
                    return v.strip().strip('"').strip("'")
    return None


class LLMClient(Protocol):
    """Minimal contract: turn a system + user prompt into text."""

    def complete(self, system: str, user: str) -> str: ...


class AnthropicClaude:
    """Real Claude client. Reads the key from ANTHROPIC_API_KEY (never inline).

    The ``anthropic`` package is imported lazily so the mock path and the tests
    run without the dependency installed.
    """

    #: Models to try, in order, until one is accepted. Anthropic model IDs vary by
    #: account/API version and change over time, so we try a few known names and
    #: fall back gracefully instead of pinning one exact dated id. Override with
    #: ANTHROPIC_MODEL in .env / the environment to force a specific one.
    _CANDIDATE_MODELS = (
        "claude-sonnet-5-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-opus-5-5",
        "claude-haiku-5-5",
    )

    def __init__(self, model: str | None = None) -> None:
        key = _read_api_key()
        if not key:
            raise RuntimeError(
                "No Anthropic API key found. Copy .env.example to .env and put "
                "your key in it (ANTHROPIC_API_KEY=sk-ant-...), or set the "
                "ANTHROPIC_API_KEY environment variable. Then re-run."
            )
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'anthropic' package is not installed. Run "
                "`pip install -r requirements.txt`."
            ) from exc
        self._client = anthropic.Anthropic(api_key=key)
        # Explicit arg > ANTHROPIC_MODEL env/.env > the candidate list.
        forced = model or os.environ.get("ANTHROPIC_MODEL") or _read_model()
        self._models = (forced,) if forced else self._CANDIDATE_MODELS
        self._resolved: str | None = None

    def complete(self, system: str, user: str) -> str:
        import anthropic  # noqa: PLC0415

        # Once a model has been accepted, keep using it. Otherwise try each
        # candidate until one works (handles account/version model-name drift).
        candidates = (self._resolved,) if self._resolved else self._models
        last_err: Exception | None = None
        for name in candidates:
            try:
                msg = self._client.messages.create(
                    model=name,
                    # Large enough that a full multi-resource Terraform config is
                    # not truncated mid-file (truncation leaves unterminated HCL).
                    max_tokens=16384,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                self._resolved = name
                parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
                return "".join(parts)
            except anthropic.NotFoundError as exc:
                last_err = exc  # this model id isn't available; try the next
                continue
        raise RuntimeError(
            "None of the candidate Claude models were available on this account: "
            f"{', '.join(self._models)}. Set ANTHROPIC_MODEL in .env to a model id "
            f"your account supports. Last error: {last_err}"
        )


class MockLLM:
    """A deterministic stand-in for tests / no-key runs.

    Returns a tiny, VALID Terraform file the first time, so the validate gate
    passes and the full plumbing (prompt -> generate -> validate -> write) can be
    exercised end to end without a real model. It ignores the prompt content on
    purpose — it only proves the loop works.
    """

    def __init__(self, *, fail_first: bool = False) -> None:
        # When fail_first is True, the first response is intentionally broken so
        # tests can exercise the validate -> fix retry path.
        self._fail_first = fail_first
        self._calls = 0

    def complete(self, system: str, user: str) -> str:  # noqa: ARG002
        self._calls += 1
        if self._fail_first and self._calls == 1:
            return "```hcl\nresource \"aws_x\" \"y\" { = broken }\n```"
        return (
            "```hcl\n"
            'terraform {\n'
            '  required_providers {\n'
            '    aws = {\n'
            '      source  = "hashicorp/aws"\n'
            '      version = ">= 5.0"\n'
            "    }\n"
            "  }\n"
            "}\n\n"
            'variable "aws_region" {\n'
            '  type    = string\n'
            '  default = "us-east-1"\n'
            "}\n\n"
            'provider "aws" {\n'
            "  region = var.aws_region\n"
            "}\n"
            "```\n"
        )
