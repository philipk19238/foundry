"""A stress and failure harness for Foundry's live crawl: a fake procurement portal on this machine, a local browser
and bucket, and named scenarios run through the real host code (`Crawls.live`). It only ever talks to localhost.

`foundry.app` reads its settings when imported and needs the service's keys; the harness uses none of them, so it
sets placeholders for any that neither `.env` nor the environment holds."""

import os

PLACEHOLDERS = {
    "SERVICE_TOKEN": "stress-placeholder",
    "CREDENTIALS_KEY": "stress-placeholder",
    "OPENAI_API_KEY": "stress-placeholder",
    "KERNEL_API_KEY": "stress-placeholder",
    "KNOCK_SECRET_KEY": "sk_test_stress-placeholder",
}

for name, value in PLACEHOLDERS.items():
    os.environ.setdefault(name, value)
