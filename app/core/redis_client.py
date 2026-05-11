import redis
from app.core.config import get_settings

settings = get_settings()

# Redis connection
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis():
    """Get Redis client"""
    return redis_client


def cache_key(*args):
    """Generate cache key from arguments"""
    return ":".join(str(arg) for arg in args)


async def set_cache(key: str, value: str, ttl: int = None):
    """Set cache with TTL"""
    ttl = ttl or settings.REDIS_CACHE_TTL
    redis_client.setex(key, ttl, value)


async def get_cache(key: str):
    """Get cache value"""
    return redis_client.get(key)


async def delete_cache(key: str):
    """Delete cache key"""
    redis_client.delete(key)


async def invalidate_pattern(pattern: str):
    """Invalidate all keys matching pattern"""
    keys = redis_client.keys(pattern)
    if keys:
        redis_client.delete(*keys)
