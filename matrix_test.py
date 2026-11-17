"""Intensive cross-vendor / cross-cloud generation matrix for the demo.

Runs the LLM generator across many (cloud, service) combinations, records for
each: validated yes/no, attempts used, generated line count, and timing. Prints
a summary table and writes each result under matrix-out/<name>/main.tf.

Run (needs ANTHROPIC_API_KEY in ../.env or env):
    .\.venv\Scripts\python matrix_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from iacgen.generate import generate_iac
from iacgen.llm import AnthropicClaude

# (platform, human description) across AWS / Azure / GCP + a few data/K8s cases.
CASES: list[tuple[str, str]] = [
    # --- AWS ---
    ("aws-lambda", "AWS Lambda from an S3 zip with a versioned artifact bucket, exec role, and a live alias"),
    ("aws-ecs-fargate", "AWS ECS Fargate service running a container from ECR behind an ALB, with task + execution roles"),
    ("aws-s3-static-site", "An S3 static website bucket fronted by CloudFront with OAC and HTTPS"),
    ("aws-dynamodb", "A DynamoDB table with on-demand billing, a global secondary index, and point-in-time recovery"),
    ("aws-sqs-sns", "An SQS queue subscribed to an SNS topic, with a dead-letter queue"),
    ("aws-glue", "An AWS Glue Spark ETL job reading/writing S3, with a Glue role and a versioned script bucket"),
    # --- Azure ---
    ("azure-functions", "An Azure Function App (Linux, Python) with a storage account, app service plan, and app insights"),
    ("azure-container-app", "An Azure Container App running an image from ACR, with a managed environment and ingress"),
    ("azure-storage", "An Azure Storage account with a blob container, HTTPS-only, and a lifecycle rule"),
    # --- GCP ---
    ("gcp-cloud-run", "A Google Cloud Run service running a container from Artifact Registry, with a service account"),
    ("gcp-gcs-bucket", "A Google Cloud Storage bucket with uniform bucket-level access, versioning, and a lifecycle rule"),
    ("gcp-pubsub", "A Google Pub/Sub topic with a subscription and a dead-letter topic"),
    # --- Kubernetes ---
    ("k8s-deployment", "A Kubernetes Deployment + Service + HorizontalPodAutoscaler for a web app, via the kubernetes provider"),
]


def main() -> int:
    llm = AnthropicClaude()
    out_root = Path(__file__).resolve().parent / "matrix-out"
    out_root.mkdir(exist_ok=True)

    results = []
    for platform, describe in CASES:
        t0 = time.time()
        try:
            r = generate_iac(llm, platform=platform, describe=describe, max_attempts=3)
            elapsed = time.time() - t0
            lines = r.hcl.count("\n")
            (out_root / platform).mkdir(exist_ok=True)
            (out_root / platform / "main.tf").write_text(r.hcl, encoding="utf-8")
            results.append((platform, r.validated, r.attempts, lines, round(elapsed, 1)))
            status = "PASS" if r.validated else "FAIL"
            print(f"  {status:4}  {platform:22}  attempts={r.attempts}  lines={lines:4}  {elapsed:4.1f}s")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.time() - t0
            results.append((platform, False, 0, 0, round(elapsed, 1)))
            print(f"  ERR   {platform:22}  {type(exc).__name__}: {str(exc)[:60]}")

    # Summary
    passed = sum(1 for _, ok, *_ in results if ok)
    print("\n" + "=" * 60)
    print(f"MATRIX RESULT: {passed}/{len(results)} validated")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
