import psycopg
from psycopg_pool import ConnectionPool
import redis
from config import settings

# Connection Pools / Clients
_pg_pool = None
_redis_client = None


def get_pg_connection():
    """Get a connection from the PostgreSQL pool."""
    global _pg_pool
    if _pg_pool is None:
        conninfo = settings.database_url or (
            f"dbname={settings.postgres_db} user={settings.postgres_user} "
            f"password={settings.postgres_password} host={settings.postgres_host} "
            f"port={settings.postgres_port}"
        )
        _pg_pool = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=20,
            open=True,
        )
    return _pg_pool.getconn()


def release_pg_connection(conn):
    """Release a PostgreSQL connection back to the pool."""
    global _pg_pool
    if _pg_pool and conn:
        _pg_pool.putconn(conn)


def get_neo4j_driver():
    """
    Stub — Neo4j replaced by the doc_graph PostgreSQL table.
    Kept for import compatibility; always returns None.
    """
    return None


def get_redis_client():
    """Get the Redis client instance."""
    global _redis_client
    if _redis_client is None:
        if settings.redis_url:
            _redis_client = redis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                protocol=2,
                socket_connect_timeout=5,
            )
        else:
            _redis_client = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=True,
                protocol=2,
                socket_connect_timeout=5,
            )
    return _redis_client


def close_connections():
    """Close all database connections."""
    global _pg_pool, _redis_client
    if _pg_pool:
        _pg_pool.close()
        _pg_pool = None
    if _redis_client:
        _redis_client.close()
        _redis_client = None


def get_postgres_dsn():
    """Get connection string for migrations."""
    if settings.database_url:
        return settings.database_url
    return (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
