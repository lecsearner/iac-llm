"""Mock-based tests for the generate -> validate -> fix loop (no API key/cost).

These exercise the full plumbing with MockLLM and the real `terraform validate`
gate, so the loop is verified without calling Claude.
"""

from __future__ import annotations

from iacgen.generate import generate_iac, _extract_hcl
from iacgen.llm import MockLLM


def test_happy_path_validates_first_try():
    r = generate_iac(MockLLM(), platform="aws-apprunner", describe="x", max_attempts=3)
    assert r.validated is True
    assert r.attempts == 1
    assert "terraform" in r.hcl


def test_retry_recovers_after_an_invalid_first_attempt():
    r = generate_iac(MockLLM(fail_first=True), platform="aws-apprunner",
                     describe="x", max_attempts=3)
    assert r.validated is True
    assert r.attempts == 2
    assert r.history == ["attempt 1: INVALID", "attempt 2: VALID"]


def test_extract_hcl_pulls_code_block():
    assert _extract_hcl("```hcl\nfoo = 1\n```").strip() == "foo = 1"
    assert _extract_hcl("no fences here").strip() == "no fences here"
