from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    key_pepper: str = ""
    admin_token: str
    rotation_grace_seconds: int = 86400
    redis_timeout_seconds: float = 0.25
    request_marker_ttl_seconds: int = 60


settings = Settings()  # type: ignore[call-arg]
