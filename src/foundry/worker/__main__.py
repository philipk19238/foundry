"""`python -m foundry.worker secrets`: publish this machine's settings as Foundry's named Modal secrets."""

import argparse
import asyncio

from foundry.app.settings import settings
from foundry.worker import worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="foundry.worker")
    parser.add_argument("command", choices=["secrets"])
    parser.parse_args()
    asyncio.run(worker.publish_secrets(settings))
    environment = settings.modal_environment
    print(f"Published the {worker.KEYS} and {worker.DEPLOY_KEYS} secrets to Modal's {environment} environment.")


if __name__ == "__main__":
    main()
