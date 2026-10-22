"""What differs per environment. Foundry is a dev-only demo: it refuses any key that is not a development key."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Read from `.env`, then `.env.shared` (keys shared with another project, e.g. a symlink to its env file), then
    the process environment. Both files are git-ignored. The files win over the environment, so a stale key exported
    machine-wide (launchctl) can't shadow this project's own; on Modal there are no files and the environment is all
    there is."""

    model_config = SettingsConfigDict(env_file=(ROOT / ".env.shared", ROOT / ".env"), extra="ignore")

    database_url: str = f"sqlite+aiosqlite:///{ROOT / 'foundry.db'}"
    base_url: str = "http://localhost:8000"
    files_dir: Path = ROOT / "files"
    files_storage: Literal["folder", "r2"] = "folder"
    files_bucket: str | None = None
    r2_account_id: str | None = None
    r2_access_key_id: SecretStr | None = None
    r2_secret_access_key: SecretStr | None = None
    workspaces_dir: Path = ROOT / "workspaces"
    dispatcher: Literal["local", "modal"] = "local"
    repo: str = "philipk19238/foundry"
    repo_cache: Path = ROOT / ".cache" / "foundry.git"
    github_token: SecretStr | None = None
    git_author: str = "Foundry agent <pkung67@utexas.edu>"
    deployed_commit: str | None = None
    modal_environment: Literal["dev"] = "dev"
    modal_token_id: SecretStr | None = None
    modal_token_secret: SecretStr | None = None

    service_token: SecretStr
    credentials_key: SecretStr

    openai_api_key: SecretStr
    codex_model: str | None = None
    kernel_api_key: SecretStr
    knock_secret_key: SecretStr
    knock_workflow: str = "foundry-help-requested"
    knock_message_workflow: str = "foundry-agent-message"
    agentmail_api_key: SecretStr | None = None

    seed_email: str | None = None
    seed_password: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    @field_validator("knock_secret_key")
    @classmethod
    def development_knock(cls, key: SecretStr) -> SecretStr:
        if not key.get_secret_value().startswith("sk_test_"):
            raise ValueError("foundry only sends through Knock's development environment (an sk_test_ key)")
        return key


settings = Settings()
