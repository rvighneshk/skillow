from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "Skillow"
    secret_key: str = "change-this-before-deploying"
    model_api_key: str = ""
    meta_base_url: str = "https://api.meta.ai/v1"
    muse_spark_model: str = "muse-spark-1.3"
    muse_voice_model: str = "muse-voice-transcribe-1.0"
    sam_model: str = "sam-3.1"
    muse_image_model: str = "muse-image-1.0"
    max_upload_mb: int = 250
    database_url: str = "sqlite:///./skillswap.db"
    storage_dir: Path = ROOT / "storage"
    public_base_url: str = ""
    native_video: bool = True
    meta_concurrency: int = 4
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
for folder in ("videos", "frames", "uploads", "diagrams"):
    (settings.storage_dir / folder).mkdir(parents=True, exist_ok=True)
