"""CLI for iacgen.

Two modes:

  single  — one resource, one `main.tf` (the original behaviour):
      python -m iacgen.cli single --platform aws-apprunner \
          --describe "deploy a container to AWS App Runner" --out ./out [--mock]

  project — a whole wired, multi-file Terraform project for a pipeline, using the
            real application code as context:
      python -m iacgen.cli project \
          --goal "Step Functions -> Lambda checks S3 -> Glue -> DynamoDB" \
          --context ../stock-data-pipeline --out ./out/stock [--mock]

For backward compatibility, invoking with the old flags (no subcommand) still
runs single mode.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from iacgen.context import load_project_context
from iacgen.generate import generate_iac
from iacgen.llm import AnthropicClaude, LLMClient, MockLLM
from iacgen.project import generate_project


def _make_llm(mock: bool) -> tuple[LLMClient, str]:
    if mock:
        return MockLLM(), "MOCK"
    return AnthropicClaude(), "Claude"


def _run_single(args: argparse.Namespace) -> int:
    llm, mode = _make_llm(args.mock)
    print(f"[iacgen] single: generating Terraform for '{args.platform}' via {mode} ...")

    result = generate_iac(
        llm, platform=args.platform, describe=args.describe,
        max_attempts=args.max_attempts,
    )
    for line in result.history:
        print(f"    {line}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "main.tf").write_text(result.hcl, encoding="utf-8")

    if result.validated:
        print(f"[iacgen] OK — validated Terraform written to {out_dir / 'main.tf'}")
        print("[iacgen] NEXT: review it, then `terraform plan` before any apply.")
        return 0
    print(f"[iacgen] FAILED validation after {result.attempts} attempt(s).")
    print(f"[iacgen] last error:\n{result.last_error}")
    print(f"[iacgen] draft written to {out_dir / 'main.tf'} for inspection.")
    return 1


def _run_project(args: argparse.Namespace) -> int:
    llm, mode = _make_llm(args.mock)
    print(f"[iacgen] project: loading context from '{args.context}' ...")
    context = load_project_context(args.context)
    print(f"[iacgen] context: {len(context.files)} file(s), "
          f"{len(context.previews)} data preview(s)")
    print(f"[iacgen] generating wired Terraform project via {mode} ...")

    result = generate_project(
        llm, goal=args.goal, context=context, max_attempts=args.max_attempts,
    )
    for line in result.history:
        print(f"    {line}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(result.files.items()):
        target = out_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"    wrote {name}")

    if result.validated:
        print(f"[iacgen] OK — validated Terraform project written to {out_dir}")
        print("[iacgen] NEXT: review the files, set terraform.tfvars paths to your "
              "real source files, then `terraform init && terraform plan`.")
        return 0
    print(f"[iacgen] FAILED validation after {result.attempts} attempt(s).")
    print(f"[iacgen] last error:\n{result.last_error}")
    print(f"[iacgen] draft files written to {out_dir} for inspection.")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iacgen")
    sub = p.add_subparsers(dest="mode")

    sp = sub.add_parser("single", help="generate one resource into a single main.tf")
    sp.add_argument("--platform", required=True)
    sp.add_argument("--describe", required=True)
    sp.add_argument("--out", default="./out")
    sp.add_argument("--max-attempts", type=int, default=3)
    sp.add_argument("--mock", action="store_true")
    sp.set_defaults(func=_run_single)

    pp = sub.add_parser("project", help="generate a whole wired multi-file project")
    pp.add_argument("--goal", required=True, help="short description of the pipeline")
    pp.add_argument("--context", required=True,
                    help="folder with the application code to wire the infra to")
    pp.add_argument("--out", default="./out/project")
    pp.add_argument("--max-attempts", type=int, default=4)
    pp.add_argument("--mock", action="store_true")
    pp.set_defaults(func=_run_project)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()

    # Backward compat: no subcommand but --platform given -> treat as single.
    if argv and argv[0] not in {"single", "project", "-h", "--help"}:
        argv = ["single", *argv]

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
