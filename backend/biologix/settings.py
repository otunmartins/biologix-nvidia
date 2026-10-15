"""Configuration, loaded from environment variables and ``.env``.

Credentials are never written to disk by this module and never appear in a
provenance manifest; :func:`Settings.redacted` is what gets recorded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- credentials -------------------------------------------------------
    nvidia_api_key: str | None = Field(default=None, alias="NVIDIA_API_KEY")
    ngc_api_key: str | None = Field(default=None, alias="NGC_API_KEY")

    # --- ESM-2 -------------------------------------------------------------
    esm2_base_url: str = Field(
        default="https://health.api.nvidia.com/v1/biology",
        alias="BIOLOGIX_ESM2_BASE_URL",
    )
    # Probed 2026-10-14: the vendor prefix is `meta`, not `nvidia` — but that
    # hosted endpoint is end-of-life (HTTP 410 since 2026-08-25) with no
    # successor named in the response. The correct path is kept as the default
    # anyway, so the failure a user sees is "this service is gone" rather than
    # "your path is wrong"; ESM-2 now needs the self-hosted NIM container.
    esm2_path: str = Field(default="/meta/esm2-650m", alias="BIOLOGIX_ESM2_PATH")
    esm2_model: str = Field(default="esm2-650m", alias="BIOLOGIX_ESM2_MODEL")
    esm2_max_residues: int = Field(default=1022, alias="BIOLOGIX_ESM2_MAX_RESIDUES")

    # --- GenMol ------------------------------------------------------------
    genmol_base_url: str = Field(default="http://127.0.0.1:8000", alias="BIOLOGIX_GENMOL_BASE_URL")
    genmol_path: str = Field(default="/generate", alias="BIOLOGIX_GENMOL_PATH")
    genmol_model: str = Field(default="genmol-2.0.0", alias="BIOLOGIX_GENMOL_MODEL")
    genmol_hosted: bool = Field(default=False, alias="BIOLOGIX_GENMOL_HOSTED")

    # --- Boltz-2 (complex structure + binding affinity) --------------------
    boltz2_base_url: str = Field(
        default="https://health.api.nvidia.com/v1/biology",
        alias="BIOLOGIX_BOLTZ2_BASE_URL",
    )
    boltz2_path: str = Field(default="/mit/boltz2/predict", alias="BIOLOGIX_BOLTZ2_PATH")
    boltz2_model: str = Field(default="boltz2-1.10.0", alias="BIOLOGIX_BOLTZ2_MODEL")
    # Documented ceilings for the request, enforced client-side. They differ
    # between NIM releases (1.0.0 documented 5 polymers / 5 ligands; later
    # releases document 12 / 20), so they are configurable rather than baked in.
    boltz2_max_polymers: int = Field(default=12, alias="BIOLOGIX_BOLTZ2_MAX_POLYMERS")
    boltz2_max_ligands: int = Field(default=20, alias="BIOLOGIX_BOLTZ2_MAX_LIGANDS")
    boltz2_max_sequence: int = Field(default=4096, alias="BIOLOGIX_BOLTZ2_MAX_SEQUENCE")

    # --- DiffDock (blind docking) ------------------------------------------
    diffdock_base_url: str = Field(
        default="https://health.api.nvidia.com/v1/biology",
        alias="BIOLOGIX_DIFFDOCK_BASE_URL",
    )
    diffdock_path: str = Field(default="/mit/diffdock", alias="BIOLOGIX_DIFFDOCK_PATH")
    diffdock_model: str = Field(default="diffdock-2.1.0", alias="BIOLOGIX_DIFFDOCK_MODEL")

    # --- MolMIM (property-guided generation) -------------------------------
    molmim_base_url: str = Field(
        default="https://health.api.nvidia.com/v1/biology",
        alias="BIOLOGIX_MOLMIM_BASE_URL",
    )
    molmim_path: str = Field(default="/nvidia/molmim/generate", alias="BIOLOGIX_MOLMIM_PATH")
    molmim_model: str = Field(default="molmim-1.0.0", alias="BIOLOGIX_MOLMIM_MODEL")

    # --- backends ----------------------------------------------------------
    openmm_platform: str = Field(default="auto", alias="BIOLOGIX_OPENMM_PLATFORM")
    enable_nvmolkit: bool = Field(default=False, alias="BIOLOGIX_ENABLE_NVMOLKIT")

    # --- storage -----------------------------------------------------------
    data_dir: Path = Field(default=Path("./var"), alias="BIOLOGIX_DATA_DIR")
    database_url: str = Field(
        default="sqlite+pysqlite:///./var/biologix.db", alias="BIOLOGIX_DATABASE_URL"
    )
    cache_dir: Path = Field(default=Path("./var/cache"), alias="BIOLOGIX_CACHE_DIR")

    # --- server ------------------------------------------------------------
    host: str = Field(default="127.0.0.1", alias="BIOLOGIX_HOST")
    port: int = Field(default=8080, alias="BIOLOGIX_PORT")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="BIOLOGIX_CORS_ORIGINS",
    )
    job_workers: int = Field(default=2, alias="BIOLOGIX_JOB_WORKERS")
    # Off for localhost development; it must be on wherever the app is served
    # over HTTPS, or the session cookie will travel in clear text. In
    # ``production`` it defaults to on — see ``_harden_for_production``.
    session_cookie_secure: bool = Field(default=False, alias="BIOLOGIX_SESSION_COOKIE_SECURE")

    # --- deployment --------------------------------------------------------
    #: ``development`` keeps the conveniences a local run needs; ``production``
    #: turns the ones that are unsafe over a network into errors or defaults.
    #: Nothing here guesses from hostnames: a deployment states what it is.
    environment: str = Field(default="development", alias="BIOLOGIX_ENV")

    # --- reproducibility ---------------------------------------------------
    seed: int = Field(default=20260101, alias="BIOLOGIX_SEED")
    allow_network: bool = Field(default=True, alias="BIOLOGIX_ALLOW_NETWORK")

    @field_validator("openmm_platform")
    @classmethod
    def _check_platform(cls, v: str) -> str:
        allowed = {"auto", "CPU", "CUDA", "OpenCL", "Reference"}
        # Accept any case, normalise to OpenMM's own spelling.
        lookup = {a.lower(): a for a in allowed}
        key = v.strip().lower()
        if key not in lookup:
            raise ValueError(f"openmm_platform must be one of {sorted(allowed)}, got {v!r}")
        return lookup[key]

    @field_validator("environment")
    @classmethod
    def _check_environment(cls, v: str) -> str:
        allowed = {"development", "production"}
        key = v.strip().lower()
        if key not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}, got {v!r}")
        return key

    @model_validator(mode="after")
    def _harden_for_production(self) -> Settings:
        """Make the production defaults safe, and refuse the unsafe override.

        A secure cookie is not optional once a session travels over a network,
        so production defaults it on. Explicitly setting it *off* in production
        is refused rather than honoured: it is far more likely to be a leftover
        from a developer's ``.env`` than a deliberate decision to send session
        tokens in clear text. Terminating TLS at a proxy does not change this —
        the cookie is still marked for HTTPS only, which is what we want.
        """
        if not self.is_production:
            return self
        if "session_cookie_secure" in self.model_fields_set:
            if not self.session_cookie_secure:
                raise ValueError(
                    "BIOLOGIX_SESSION_COOKIE_SECURE=false is refused when "
                    "BIOLOGIX_ENV=production: the session cookie would travel in "
                    "clear text. Remove the override, or run with "
                    "BIOLOGIX_ENV=development."
                )
        else:
            self.session_cookie_secure = True
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_postgres(self) -> bool:
        """True when the store is a real database server rather than a file."""
        return self.database_url.startswith(("postgresql", "postgres://"))

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.cache_dir, self.runs_dir):
            p.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict[str, object]:
        """Configuration safe to embed in a provenance manifest."""
        data = self.model_dump(mode="json")
        for secret in ("nvidia_api_key", "ngc_api_key"):
            data[secret] = "<set>" if getattr(self, secret) else "<unset>"
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Drop the cached settings; used by tests that patch the environment."""
    get_settings.cache_clear()
