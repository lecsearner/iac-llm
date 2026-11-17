# iac-llm-gen — Getting Started (from scratch)

Turn a plain-English description of cloud infrastructure into **validated**
Terraform, using an LLM (Anthropic Claude) with a `terraform validate` safety
gate. This is the experimental, LLM-powered package. (Its deterministic sibling,
`agentic-cicd-generator`, uses fixed templates and no LLM — keep the two
distinct when you present.)

---

## What it is, in one picture

```
  You, in plain English:  "a Lambda in python 3.12 triggered by an S3 bucket"
          │
          ▼
  ┌─────────────────┐   prompt    ┌──────────────┐
  │   iacgen CLI    │ ──────────▶ │  Claude (LLM) │  drafts Terraform HCL
  └─────────────────┘             └──────┬───────┘
          ▲                               │
          │   validation error            ▼
          │   fed back for a fix   ┌──────────────────┐
          └────────────────────────│ terraform validate│  the GATE
                                   └──────┬───────────┘
                                          │ passes
                                          ▼
                                  validated main.tf
                                          │
                                 you review → terraform apply
```

**The core idea to say out loud:** the LLM *proposes*, `terraform validate`
*disposes*. The tool never hands you code that doesn't pass validation; if the
first draft fails, it feeds the error back to the LLM and retries (up to
`--max-attempts`, default 3).

---

## The three source files (so you can answer "how does it work?")

| File                | Job                                                                  |
|---------------------|----------------------------------------------------------------------|
| `iacgen/llm.py`     | Talks to the model. `LLMClient` is an interface; `AnthropicClaude` is the real client (reads the API key locally), `MockLLM` is a free offline stand-in. |
| `iacgen/generate.py`| The generate → validate → fix loop. Builds the prompt, extracts HCL, runs `terraform validate` in a temp dir, retries on failure. |
| `iacgen/cli.py`     | Command-line front door: `--platform`, `--describe`, `--out`, `--mock`, `--max-attempts`. |

Two details worth knowing because people ask:
- **The validate gate needs no cloud credentials.** It runs `terraform init
  -backend=false` + `terraform validate` in a throwaway temp dir. That checks
  syntax and provider/resource correctness without touching any account.
- **Provider downloads are cached** (`.tf-plugin-cache/`), so repeated runs don't
  re-download ~200 MB each time.

---

## Setup (one time)

```powershell
cd iac-llm-gen

# 1. virtual environment
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .

# 2. API key — create a local .env (it is git-ignored; NEVER commit it)
#    file: iac-llm-gen\.env
#    contents:
#       ANTHROPIC_API_KEY=sk-ant-...
#    (optional) ANTHROPIC_MODEL=claude-sonnet-5-5   # pin a model if you want

# 3. terraform must be installed and on PATH (or installed via winget;
#    the tool auto-discovers the winget path on Windows if PATH misses it)
terraform -version
```

Requirements: Python >= 3.11, Terraform installed, an Anthropic API key with
credit. The only runtime dependency is the `anthropic` package.

---

## The demo flow (what to show live)

### Option A — free dry run, no key, no cost

Proves the *mechanism* (prompt → generate → validate loop) using the mock LLM:

```powershell
.\.venv\Scripts\python -m iacgen.cli `
  --platform aws-lambda `
  --describe "a Lambda function in python 3.12 triggered by an S3 bucket" `
  --out .\out --mock
```

You'll see `attempt 1: VALID` and a `main.tf` written to `.\out`.

### Option B — the real thing with Claude

```powershell
.\.venv\Scripts\python -m iacgen.cli `
  --platform aws-lambda `
  --describe "a Lambda function in python 3.12 triggered by an S3 bucket" `
  --out .\out
```

Expected output:

```
[iacgen] generating Terraform for 'aws-lambda' via Claude ...
    attempt 1: VALID
[iacgen] OK — validated Terraform written to out\main.tf
[iacgen] NEXT: review it, then `terraform plan` before any apply.
```

### Then deploy it yourself (you, not the tool)

```powershell
cd out
terraform init
terraform plan        # review what will be created
terraform apply       # type 'yes' to create it for real
```

And to clean up afterward:

```powershell
terraform destroy
```

---

## Rebuilding the full connected pipeline (the big demo)

The headline demo is a connected AWS pipeline, every `.tf` LLM-generated:

```
S3 upload → EventBridge → Step Functions → Lambda → DynamoDB (routing table)
```

Each piece is generated the same way — one `iacgen` call per service, each into
its own `--out` dir — then applied in dependency order
(DynamoDB → Lambda → Step Functions → S3/EventBridge). The handler code and the
exact describe-prompts live under `demo/pipeline/`. The full run book, live
commands, and the end-to-end proof (the Lambda log showing `from_table: true`)
are in **`demo/DEMO_SCRIPT.md`** — follow that to rebuild and run it live.

---

## Answers to the questions people will ask

**"Is this just ChatGPT writing Terraform?"**
No. The difference is the validation gate and the retry loop — it never ships
code that fails `terraform validate`, and it self-corrects from the error.

**"Can it deploy on its own?"**
No, by design. It generates and validates; a human reviews and runs `apply`.
That boundary is deliberate for an experimental tool.

**"What if the LLM invents a resource that doesn't exist?"**
`terraform validate` catches structural/provider errors immediately and the
error is fed back for a fix. It will NOT catch a config that is valid but
semantically wrong — which is exactly why a human reviews before apply.

**"Do my API key or cloud credentials go to a third party?"**
No. The Anthropic key is read from a local `.env`. `terraform apply` runs on
your machine against your own cloud credentials. The generator only sends your
*description* to the model, never your secrets.

**"Why is it non-deterministic?"**
It's an LLM — ask twice, get slightly different (both valid) code. That's why
it's the experiment and `agentic-cicd-generator` (fixed templates, identical
output every time) is the trusted production path.

**"Which platforms does it support?"**
Anything the model knows and the Terraform providers cover — AWS, Azure, GCP,
Kubernetes. You just change `--platform` and `--describe`. No per-platform
plugin to write (that's the trade-off vs. the deterministic package).

**"What were the limitations in the demo?"**
Two honest ones: email delivery was logged not sent (SES sandbox on the demo
account), and the Databricks job was generated + validated but not applied
(Free Edition is serverless-only and rejects the classic cluster). Neither is a
generator defect.
```