"""Project mode: one LLM run -> a complete, wired, multi-file Terraform project.

Unlike single-file mode (``generate.py``), this produces a whole directory —
``main.tf`` / ``variables.tf`` / ``outputs.tf`` / ``terraform.tfvars`` and any
template files — for a multi-resource pipeline whose resources reference each
other (Lambda ARN -> state machine, Glue job name -> ASL, table name -> both,
bucket -> all). The application code is fed in as context so the generated
infra matches the real handler names, script paths, ASL placeholders and table
keys.

The output is still gated by ``terraform validate`` over the *whole directory*,
with the same feed-the-error-back retry loop. It remains non-deterministic and
a human must review the plan before applying — validate proves the config is
well-formed, not that it is semantically what you intended.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from iacgen.context import ProjectContext
from iacgen.llm import LLMClient

_SYSTEM = """\
You are a senior infrastructure engineer. You generate a COMPLETE, MULTI-FILE
Terraform project for an entire pipeline in ONE response. The resources must be
WIRED to each other — never leave a cross-resource value hard-coded or TODO.

Output format — STRICT. Return ONLY a single JSON object, no prose, no markdown
fences, mapping each filename to its full text content:

{
  "main.tf":           "...",
  "variables.tf":      "...",
  "outputs.tf":        "...",
  "terraform.tfvars":  "...",
  "<name>.tftpl":      "..."   // only if you use templatefile()
}

Rules:
- Split correctly: all `variable` blocks in variables.tf, all `output` blocks in
  outputs.tf, the `terraform{}`+`provider{}`+resources/data in main.tf, and
  concrete values in terraform.tfvars.
- Pin every provider in a required_providers block (AWS version >= 5.0).
- WIRE resources by reference: e.g. the state machine's definition must receive
  the Lambda ARN, the Glue job name and the DynamoDB table name via
  `templatefile(...)` with the state machine's own resource attributes — NOT
  literal strings. The Glue job's DDB_TABLE argument must reference the table
  resource. IAM policies must grant exactly the actions the code performs.
- PACKAGE + UPLOAD artifacts in Terraform so a single `terraform apply` is
  self-contained:
    * Zip the Lambda source with an `archive_file` data source and deploy it via
      `filename`/`source_code_hash`.
    * Upload the Glue script and any seed data file to S3 with `aws_s3_object`
      resources, then reference those object keys where needed (the Glue job's
      script_location, the sample data key, etc.).
- Parameterise account-specific values (region, names, source paths) as
  variables with sensible defaults; put the concrete choices in terraform.tfvars.
- NEVER inline a real secret, access key, or account id. Least-privilege IAM; no
  public buckets; no wildcard admin.
- Paths to local source files (Lambda dir, Glue script, ASL file, CSV) must be
  variables so the user can point them at their checkout; default them to the
  relative paths shown in the project context.
If given a previous attempt and a validation error, return the SAME JSON shape
with only the fixes needed to make `terraform validate` pass."""


@dataclass
class ProjectResult:
    """Outcome of a project-mode generation run."""

    files: dict[str, str]
    attempts: int
    validated: bool
    last_error: str = ""
    history: list[str] = field(default_factory=list)


def _terraform_bin() -> str:
    found = shutil.which("terraform")
    if found:
        return found
    base = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if base.is_dir():
        for hit in base.rglob("terraform.exe"):
            return str(hit)
    return "terraform"


_PLUGIN_CACHE = Path(__file__).resolve().parents[1] / ".tf-plugin-cache"


def _extract_json(text: str) -> dict[str, str]:
    """Pull the JSON file-map out of the model reply, tolerating fences/prose."""
    # Strip a ```json ... ``` fence if present.
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    candidate = candidate.strip()
    # If there is leading/trailing prose, grab the outermost {...}.
    if not candidate.startswith("{"):
        brace = re.search(r"\{.*\}", candidate, re.DOTALL)
        if brace:
            candidate = brace.group(0)
    data = json.loads(candidate)
    if not isinstance(data, dict) or not data:
        raise ValueError("model did not return a non-empty JSON file map")
    # Keep only string values; coerce everything to str content.
    return {str(k): (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}


def validate_project(files: dict[str, str]) -> tuple[bool, str]:
    """Write all files to a temp dir and run terraform validate over the whole dir.

    Uses `init -backend=false` + `validate`, so no remote state and no cloud
    credentials are needed — this gate only checks that the configuration is
    well-formed and internally consistent.
    """
    tf = _terraform_bin()
    _PLUGIN_CACHE.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": str(_PLUGIN_CACHE)}
    with tempfile.TemporaryDirectory(prefix="iacgen-proj-") as d:
        dpath = Path(d)
        for name, content in files.items():
            target = dpath / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        # Provide placeholder local source files referenced by archive_file /
        # aws_s3_object so validate does not fail on a missing path. These are
        # only for the validate sandbox; the real files are used at apply time.
        _seed_referenced_paths(dpath, files)
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


def _seed_referenced_paths(dpath: Path, files: dict[str, str]) -> None:
    """Create empty stand-ins for local paths the config references.

    `terraform validate` evaluates `archive_file`/`templatefile` data sources and
    fails if the referenced path is absent. We create harmless placeholders in
    the sandbox so validation reflects config correctness, not a missing local
    file (the real files are supplied at apply time via tfvars).
    """
    joined = "\n".join(files.values())
    # Default relative paths the model was told to use; create them if referenced.
    for rel in set(re.findall(r'default\s*=\s*"([^"]+\.(?:py|json|csv|zip))"', joined)):
        p = dpath / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("", encoding="utf-8")
    # Also any ./path style strings used directly.
    for rel in set(re.findall(r'"(\.\/[^"]+\.(?:py|json|csv|tftpl))"', joined)):
        p = dpath / rel.lstrip("./")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("", encoding="utf-8")


def generate_project(
    llm: LLMClient,
    *,
    goal: str,
    context: ProjectContext,
    max_attempts: int = 3,
) -> ProjectResult:
    """Generate a whole wired Terraform project and gate it with validate.

    ``goal`` is a short description of the pipeline; ``context`` carries the real
    application files so the infra wires to them. The loop prompts, parses the
    JSON file map, validates the whole directory, and on failure feeds the error
    back for a fix — up to ``max_attempts``.
    """
    base_user = (
        f"PIPELINE GOAL:\n{goal}\n\n"
        f"{context.render()}\n\n"
        "Generate the complete wired Terraform project now as the JSON file map."
    )
    user = base_user
    history: list[str] = []
    files: dict[str, str] = {}
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        raw = llm.complete(_SYSTEM, user)
        try:
            files = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = f"could not parse JSON file map: {exc}"
            history.append(f"attempt {attempt}: PARSE-FAIL")
            user = (
                base_user
                + "\n\nYour previous reply was not a single valid JSON object "
                "mapping filenames to contents. Return ONLY that JSON object."
            )
            continue

        ok, message = validate_project(files)
        history.append(f"attempt {attempt}: {'VALID' if ok else 'INVALID'}")
        if ok:
            return ProjectResult(files=files, attempts=attempt, validated=True, history=history)
        last_error = message
        user = (
            base_user
            + "\n\nYour previous project failed `terraform validate` with:\n"
            f"-----\n{message}\n-----\n"
            "Here is what you produced:\n"
            f"{json.dumps(files, indent=2)}\n\n"
            "Return the corrected JSON file map (same shape), fixing only what is "
            "needed to make validate pass."
        )

    return ProjectResult(
        files=files, attempts=max_attempts, validated=False,
        last_error=last_error, history=history,
    )
