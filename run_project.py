"""Tiny runner so the shell doesn't choke on a long inline --goal argument.

Usage:
    python run_project.py <goal_file> <context_dir> <out_dir> [--mock]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from iacgen.context import ProjectContext, load_project_context
from iacgen.llm import AnthropicClaude, MockLLM
from iacgen.project import ProjectResult, generate_project


def _write_run_report(out: Path, context: ProjectContext, result: ProjectResult,
                      goal: str, mock: bool) -> None:
    """Persist the run's metadata next to the generated files.

    The generated .tf files are the product; this report captures everything
    else about HOW they were produced — the attempt history (proof of the
    validate/retry loop), whether it validated, the last error, and which
    context files the model saw. Written as run-report.json in the output dir.
    """
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "llm": "mock" if mock else "claude",
        "goal": goal,
        "context_root": str(context.root),
        "context_files": [rel for rel, _ in context.files],
        "context_previews": [rel for rel, _ in context.previews],
        "attempts": result.attempts,
        "validated": result.validated,
        "history": result.history,
        "generated_files": sorted(result.files.keys()),
        "last_error": result.last_error,
    }
    (out / "run-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


def main() -> int:
    goal_file, context_dir, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    mock = "--mock" in sys.argv[4:]

    goal = Path(goal_file).read_text(encoding="utf-8").strip()
    context = load_project_context(context_dir)
    print(f"[run] context: {[f[0] for f in context.files]}")
    print(f"[run] data previews: {[p[0] for p in context.previews]}")

    llm = MockLLM() if mock else AnthropicClaude()
    print(f"[run] generating via {'MOCK' if mock else 'Claude'} ...")
    result = generate_project(llm, goal=goal, context=context, max_attempts=4)

    for line in result.history:
        print(f"    {line}")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(result.files.items()):
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"    wrote {name}")

    _write_run_report(out, context, result, goal, mock)
    print(f"    wrote run-report.json")

    if result.validated:
        print(f"[run] OK — validated project in {out}")
        return 0
    print(f"[run] FAILED after {result.attempts} attempts")
    print(f"[run] last error:\n{result.last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
