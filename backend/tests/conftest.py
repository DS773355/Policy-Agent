import pytest
from unittest.mock import patch, MagicMock
from main import app
from services.auth import get_current_user

class MockRedis:
    def __init__(self, *args, **kwargs):
        self.store = {}

    def ping(self):
        return True

    def get(self, key):
        val = self.store.get(key)
        if val is None:
            return None
        return str(val).encode('utf-8')

    def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True

    def incr(self, key):
        val = int(self.store.get(key, 0)) + 1
        self.store[key] = val
        return val

    def expire(self, key, ttl):
        return True

    def sadd(self, key, member):
        if key not in self.store:
            self.store[key] = set()
        self.store[key].add(member)
        return True

    def smembers(self, key):
        return self.store.get(key, set())

    def hset(self, name, key, value):
        if name not in self.store:
            self.store[name] = {}
        self.store[name][key] = value
        return 1

    def hgetall(self, name):
        return self.store.get(name, {})

    def lrange(self, key, start, end):
        lst = self.store.get(key, [])
        return [str(x).encode('utf-8') for x in lst[start:end+1]]

    def rpush(self, key, *values):
        if key not in self.store:
            self.store[key] = []
        for val in values:
            self.store[key].append(val)
        return len(self.store[key])

    def ltrim(self, key, start, end):
        if key in self.store:
            self.store[key] = self.store[key][start:end+1]
        return True

@pytest.fixture(autouse=True)
def mock_redis_and_auth(request):
    """
    Mock redis client and auth dependencies.
    Bypasses auth overrides for test_integration.py to test token validations.
    """
    mock_redis_instance = MockRedis()
    mock_pg_conn = MagicMock()
    
    # Only override auth if we are not running integration tests
    is_integration = "test_integration" in request.node.fspath.strpath
    if not is_integration:
        mock_user = {"id": 1, "username": "admin_user", "role": "admin"}
        app.dependency_overrides[get_current_user] = lambda: mock_user
    
    # Patch all database connection layers
    with patch("database.get_redis_client", return_value=mock_redis_instance), \
         patch("main.get_redis_client", return_value=mock_redis_instance), \
         patch("services.rag_service.get_redis_client", return_value=mock_redis_instance), \
         patch("services.memory_service.get_redis_client", return_value=mock_redis_instance), \
         patch("database.get_pg_connection", return_value=mock_pg_conn), \
         patch("main.get_pg_connection", return_value=mock_pg_conn), \
         patch("services.auth.get_pg_connection", return_value=mock_pg_conn), \
         patch("services.document_service.get_pg_connection", return_value=mock_pg_conn), \
         patch("services.memory_service.get_pg_connection", return_value=mock_pg_conn), \
         patch("services.rag_service.get_pg_connection", return_value=mock_pg_conn):
        yield
        
    app.dependency_overrides.clear()
