from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os

class Settings(BaseSettings):
    # PostgreSQL Configuration
    postgres_db: str = Field(default="policy_db", validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="postgres", validation_alias="POSTGRES_USER")
    postgres_password: str = Field(default="postgres_password", validation_alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, validation_alias="POSTGRES_PORT")

    # Neo4j Configuration
    neo4j_uri: str = Field(default="bolt://localhost:7687", validation_alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", validation_alias="NEO4J_USER")
    neo4j_password: str = Field(default="neo4j_password", validation_alias="NEO4J_PASSWORD")

    # Redis Configuration
    redis_host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")

    # llama.cpp Inference Server Configuration (Phi-3)
    vllm_api_url: str = Field(default="http://localhost:8080/v1", validation_alias="VLLM_API_URL")

    # Business Logic Thresholds
    impact_score_threshold: float = Field(default=0.4, validation_alias="IMPACT_SCORE_THRESHOLD")
    overlap_similarity_threshold: float = Field(default=0.88, validation_alias="OVERLAP_SIMILARITY_THRESHOLD")
    frozen_memory_similarity_threshold: float = Field(default=0.95, validation_alias="FROZEN_MEMORY_SIMILARITY_THRESHOLD")
    dbscan_eps: float = Field(default=0.15, validation_alias="DBSCAN_EPS")
    consolidation_min_samples: int = Field(default=2, validation_alias="CONSOLIDATION_MIN_SAMPLES")
    rerank_top_k: int = Field(default=4, validation_alias="RERANK_TOP_K")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings
settings = Settings()
