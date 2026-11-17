"""The generate -> validate -> fix loop: LLM drafts Terraform, `terraform
validate` gates it, failures are fed back for a retry."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from iacgen.llm import LLMClient

_SYSTEM = """\
You are a senior infrastructure engineer. You output ONLY valid Terraform HCL for
the requested platform — no explanations, no markdown prose outside a single
```hcl code block. Follow these rules strictly:
- Target the AWS/Azure/GCP provider appropriate to the platform; pin the provider
  with a `required_providers` block and version `>= 5.0` (AWS) or current major.
- Parameterise every account-specific value (region, names, ids) as `variable`
  blocks with sensible defaults. NEVER inline a real account id, ARN, secret, or
  access key.
- Produce a single self-contained configuration (provider + variables + the
  resources the platform needs + useful outputs).
- Prefer least-privilege IAM. Do not create public buckets or wildcard admin.
If given a previous attempt and a validation error, fix ONLY what is needed to
make `terraform validate` pass, keeping the rest stable."""


@dataclass
class GenResult:
    """Outcome of a generation run."""

    hcl: str
    attempts: int
    validated: bool
    last_error: str = ""
    history: list[str] = field(default_factory=list)


def _extract_hcl(text: str) -> str:
    """Pull the HCL out of a ```hcl ...``` block, or return the text as-is."""
    m = re.search(r"```(?:hcl|terraform|tf)?\s*\n(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip() + "\n"


def _terraform_bin() -> str:
    found = shutil.which("terraform")
    if found:
        return found
    base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if base.is_dir():
        for hit in base.rglob("terraform.exe"):
            return str(hit)
    return "terraform"


# Persistent provider cache so `terraform init` downloads each provider ONCE,
# not on every validate call (otherwise each gate re-fetches ~200MB — very slow).
_PLUGIN_CACHE = Path(__file__).resolve().parents[1] / ".tf-plugin-cache"


def terraform_validate(hcl: str) -> tuple[bool, str]:
    """Run `terraform validate` on HCL in a temp dir. Returns (ok, message).

    Uses `init -backend=false` (no remote state, no cloud creds needed) then
    `validate`. This is the automatic GATE — it catches syntax/reference errors
    the model hallucinates, without touching any real account. A persistent
    TF_PLUGIN_CACHE_DIR means providers are downloaded once and reused, so repeat
    calls are fast.
    """
    tf = _terraform_bin()
    _PLUGIN_CACHE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": str(_PLUGIN_CACHE)}
    with tempfile.TemporaryDirectory(prefix="iacgen-") as d:
        (Path(d) / "main.tf").write_text(hcl, encoding="utf-8")
        init = subprocess.run(
            [tf, "init", "-backend=false", "-input=false", "-no-color"],
            cwd=d, text=True, capture_output=True, env=env,
        )
        if init.returncode != 0:
            return False, (init.stdout + init.stderr).strip()
        val = subprocess.run(
            [tf, "validate", "-no-color"],
            cwd=d, text=True, capture_output=True, env=env,
        )
        return val.returncode == 0, (val.stdout + val.stderr).strip()


def generate_iac(
    llm: LLMClient,
    *,
    platform: str,
    describe: str,
    max_attempts: int = 3,
) -> GenResult:
    """Generate Terraform for ``platform`` and gate it with `terraform validate`.

    The loop: prompt -> HCL -> validate. On failure, the validation error is
    appended to the next prompt so the model can fix it. Stops at first success
    or after ``max_attempts``.
    """
    user = (
        f"Platform: {platform}\n"
        f"Requirement: {describe}\n\n"
        "Produce the Terraform now."
    )
    history: list[str] = []
    hcl = ""
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        raw = llm.complete(_SYSTEM, user)
        hcl = _extract_hcl(raw)
        ok, message = terraform_validate(hcl)
        history.append(f"attempt {attempt}: {'VALID' if ok else 'INVALID'}")
        if ok:
            return GenResult(hcl=hcl, attempts=attempt, validated=True, history=history)
        last_error = message
        # Feed the failure back for the next attempt.
        user = (
            f"Platform: {platform}\n"
            f"Requirement: {describe}\n\n"
            "Your previous Terraform failed `terraform validate` with:\n"
            f"-----\n{message}\n-----\n"
            "Here is what you produced:\n"
            f"```hcl\n{hcl}```\n\n"
            "Return the corrected, fully valid Terraform."
        )

    return GenResult(
        hcl=hcl, attempts=max_attempts, validated=False,
        last_error=last_error, history=history,
    )
