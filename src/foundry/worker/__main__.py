"""`python -m foundry.worker secrets|deploy`: publish this machine's settings as Foundry's named Modal secrets, or
deploy the Modal app. Both use the Modal token in the settings, the same one the redeploy function gets, so every
deploy lands in one workspace."""

import argparse
import asyncio
import os
import subprocess

from foundry.app.settings import ROOT, settings
from foundry.worker import worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="foundry.worker")
    parser.add_argument("command", choices=["secrets", "deploy"])
    options = parser.parse_args()
    if settings.modal_token_id is not None and settings.modal_token_secret is not None:
        os.environ["MODAL_TOKEN_ID"] = settings.modal_token_id.get_secret_value()
        os.environ["MODAL_TOKEN_SECRET"] = settings.modal_token_secret.get_secret_value()
    environment = settings.modal_environment
    match options.command:
        case "secrets":
            asyncio.run(worker.publish_secrets(settings))
            print(f"Published the {worker.KEYS} and {worker.DEPLOY_KEYS} secrets to Modal's {environment} environment.")
        case "deploy":
            command = ["modal", "deploy", "--env", environment, worker.FUNCTIONS]
            raise SystemExit(subprocess.run(command, cwd=ROOT, check=False).returncode)


if __name__ == "__main__":
    main()
