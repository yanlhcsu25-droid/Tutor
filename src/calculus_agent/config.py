from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALCULUS_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Chinese Math Paper Agent"
    database_url: str = "sqlite:///./calculus_agent.db"

    # Phase 2C remains gated until the Phase 2B.1 human calibration passes.
    phase2c_enabled: bool = False

    # OCR protection (Level 1: monitored in-process page workers).
    ocr_page_timeout_seconds: float = 300.0
    ocr_page_rss_limit_mb: int = 8192

    # Ollama 配置
    ollama_base_url: str = "http://127.0.0.1:11434"
    solver_model: str = "qwen3:14b"
    solver_timeout_seconds: float = 120.0

    # PDF 配置
    pdf_engine: str = "auto"
    pdf_compile_timeout_seconds: float = 60.0

    # 百炼配置
    bailian_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_BAILIAN_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_agent_model: str = "qwen-plus"
    bailian_vision_model: str = "qwen3-vl-plus"
    bailian_timeout_seconds: float = 120.0

    # SiliconFlow 配置
    siliconflow_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_SILICONFLOW_API_KEY",
            "SILICONFLOW_API_KEY",
        ),
    )
    siliconflow_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_SILICONFLOW_BASE_URL",
            "SILICONFLOW_BASE_URL",
        ),
    )
    siliconflow_vl_model: str = Field(
        default="zai-org/GLM-4.5V",
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_SILICONFLOW_VL_MODEL",
            "SILICONFLOW_VL_MODEL",
        ),
    )
    siliconflow_agent_model: str = Field(
        default="zai-org/GLM-4.5V",
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_SILICONFLOW_AGENT_MODEL",
            "SILICONFLOW_AGENT_MODEL",
        ),
    )
    siliconflow_timeout_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices(
            "CALCULUS_AGENT_SILICONFLOW_TIMEOUT_SECONDS",
            "SILICONFLOW_TIMEOUT_SECONDS",
        ),
    )

    # 外部数据集路径
    external_data_root: Path = Path("../data/external/ugmathbench")


@lru_cache
def get_settings() -> Settings:
    return Settings()
