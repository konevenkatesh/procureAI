"""
Application configuration loaded from .env via pydantic-settings.

NOTE: No Anthropic / OpenAI API keys here. The LLM work in this project is
done by Claude Code in conversation — scripts write batch files, the
operator (Claude Code) reads them and writes results back.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres
    postgres_password: str = "changeme"
    postgres_url: str = "postgresql://apuser:changeme@localhost:5432/ap_knowledge"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "clause_concepts"

    # Fuseki
    fuseki_url: str = "http://localhost:3030"
    fuseki_password: str = "admin"
    fuseki_dataset: str = "ap_procurement"

    # Embeddings
    bge_m3_model: str = "BAAI/bge-m3"
    bge_m3_dim: int = 1024

    # Logging
    log_level: str = "INFO"

    # ── Paths (computed) ────────────────────────────────────────────────────
    repo_root: Path = REPO_ROOT
    source_documents_dir: Path = REPO_ROOT / "source_documents"
    data_dir: Path = REPO_ROOT / "data"
    ontology_dir: Path = REPO_ROOT / "ontology"

    @property
    def extraction_batches_dir(self) -> Path:
        return self.data_dir / "extraction_batches"

    @property
    def extraction_results_dir(self) -> Path:
        return self.data_dir / "extraction_results"

    @property
    def clause_batches_dir(self) -> Path:
        return self.data_dir / "clause_batches"

    @property
    def clause_results_dir(self) -> Path:
        return self.data_dir / "clause_results"

    @property
    def telugu_batches_dir(self) -> Path:
        return self.data_dir / "telugu_batches"

    @property
    def telugu_results_dir(self) -> Path:
        return self.data_dir / "telugu_results"

    @property
    def shacl_batches_dir(self) -> Path:
        return self.data_dir / "shacl_batches"

    @property
    def shacl_results_dir(self) -> Path:
        return self.data_dir / "shacl_results"

    @property
    def testcase_batches_dir(self) -> Path:
        return self.data_dir / "testcase_batches"

    @property
    def testcase_results_dir(self) -> Path:
        return self.data_dir / "testcase_results"

    @property
    def shacl_shapes_dir(self) -> Path:
        return self.ontology_dir / "shacl_shapes"


settings = Settings()
