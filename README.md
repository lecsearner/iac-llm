# iac-llm-gen — LLM-generated Terraform (experimental)

A standalone prototype that generates Terraform for an arbitrary cloud/platform
by asking an LLM (Anthropic Claude), then running it through a `terraform
validate` gate with an automatic generate -> validate -> fix retry loop.

This is **Option 1** (pure LLM generation) deliberately kept SEPARATE from the
deterministic `agentic-cicd-generator` so the trusted template engine is never
put at risk. It is experimental: output is non-deterministic and must be reviewed
and `terraform plan`-ed before you ever apply it to a real account.

## How it works

```
describe what you want ──▶ prompt Claude ──▶ Terraform HCL
                                               │
                                               ▼
                                   terraform validate  (the gate)
                                       │           │
                                   passes        fails
                                       │           │
                                       ▼           ▼
                                   write to    feed error back to Claude,
                                   out/        retry (up to N times)
```

## What YOU need to provide

1. **An Anthropic API key.** Create one at https://console.anthropic.com/
   (Settings -> API Keys). It is pay-as-you-go; a few test generations cost
   cents. Set it as an environment variable — never paste it into code:
   ```
   # PowerShell
   $env:ANTHROPIC_API_KEY = "sk-ant-..."
   ```
2. **Terraform installed** (already on this machine).
3. **Python 3.11+** (already here).

## Install

```
cd iac-llm-gen
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

## Run

Try the plumbing with the built-in MOCK (no API key, no cost) first:
```
.\.venv\Scripts\python -m iacgen.cli --platform aws-apprunner \
    --describe "deploy a container image to AWS App Runner" \
    --out ./out --mock
```

Then the real thing (needs ANTHROPIC_API_KEY):
```
.\.venv\Scripts\python -m iacgen.cli --platform aws-apprunner \
    --describe "deploy a container image to AWS App Runner" \
    --out ./out
```

## Honest limitations (read before applying anything)

- **Non-deterministic**: each run may produce different HCL.
- **`validate` is not `plan`**: valid HCL can still be wrong (bad IAM scope,
  unwanted defaults). ALWAYS run `terraform plan` and read it before `apply`.
- **No human gate by default**: this is pure Option 1 by design. The only
  automatic safety is `terraform validate` + the retry loop.
- **Costs money + needs internet**: every generation is a Claude API call.
- Treat all output as a DRAFT to review, not production-ready infrastructure.
