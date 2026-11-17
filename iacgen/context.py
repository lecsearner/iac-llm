"""Load a project's files into prompt context so the LLM wires infrastructure to
the real code — real Lambda handler names, real Glue script, the real Step
Functions ASL and its ``${...}`` placeholders, the real DynamoDB keys.

This is what lets a single run build a *connected* pipeline instead of guessing:
the model sees what the application code actually does and generates Terraform
that matches it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# File types worth showing the model, and a cap so one giant file can't blow the
# context budget. Data files (CSV) are included by name/preview only — the model
# needs to know they exist and their shape, not every row.
_TEXT_SUFFIXES = {".py", ".json", ".asl", ".md", ".txt", ".yaml", ".yml", ".tf", ".sh", ".toml"}
_PREVIEW_SUFFIXES = {".csv", ".tsv", ".parquet"}
_SKIP_DIRS = {".git", "__pycache__", ".venv", ".terraform", "node_modules", ".idea", ".vscode"}
_MAX_FILE_CHARS = 20_000
_PREVIEW_LINES = 8


@dataclass
class ProjectContext:
    """The files gathered from a project folder, ready to render into a prompt."""

    root: Path
    files: list[tuple[str, str]] = field(default_factory=list)  # (relpath, content)
    previews: list[tuple[str, str]] = field(default_factory=list)  # (relpath, head)

    def render(self) -> str:
        """Render the gathered context as a single prompt-friendly block."""
        parts: list[str] = [
            f"PROJECT CONTEXT (root: {self.root.name})",
            "The following are the application's actual files. Generate Terraform "
            "that matches these exactly — real handler names, real script paths, "
            "real state-machine placeholders, real table keys.\n",
        ]
        for rel, content in self.files:
            parts.append(f"--- FILE: {rel} ---\n{content}\n")
        for rel, head in self.previews:
            parts.append(f"--- DATA FILE (preview): {rel} ---\n{head}\n")
        return "\n".join(parts)


def load_project_context(folder: str | Path) -> ProjectContext:
    """Walk ``folder`` and collect text files (full) and data files (preview).

    Raises FileNotFoundError if the folder does not exist, so a bad --context
    path fails loudly rather than silently producing context-free output.
    """
    root = Path(folder).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Context folder not found: {root}")

    ctx = ProjectContext(root=root)
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        suffix = path.suffix.lower()

        if suffix in _TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > _MAX_FILE_CHARS:
                text = text[:_MAX_FILE_CHARS] + "\n... [truncated] ..."
            ctx.files.append((rel, text))
        elif suffix in _PREVIEW_SUFFIXES:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            head = "\n".join(lines[:_PREVIEW_LINES])
            ctx.previews.append((rel, head))

    return ctx
