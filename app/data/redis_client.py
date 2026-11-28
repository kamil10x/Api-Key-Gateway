from functools import lru_cache
from typing import TypeAlias

import redis

from app.core.config import settings

RedisClient: TypeAlias = redis.Redis


def build_redis(url: str, timeout: float) -> RedisClient:
    return redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=timeout, socket_connect_timeout=timeout
    )


@lru_cache(maxsize=1)
def get_redis() -> RedisClient:
    return build_redis(settings.redis_url, settings.redis_timeout_seconds)
