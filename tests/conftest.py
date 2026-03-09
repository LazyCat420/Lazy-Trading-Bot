import pytest
from app.config import settings

@pytest.fixture(autouse=True, scope="session")
def use_test_db(tmp_path_factory):
    # Route all database operations in tests to a temporary DuckDB file
    # This prevents 'database is locked' errors when the live server is running
    temp_dir = tmp_path_factory.mktemp("test_db")
    test_db_path = temp_dir / "test_trading_bot.duckdb"
    
    # Overwrite the global settings DB_PATH
    settings.DB_PATH = test_db_path
    
    # Let tests run
    yield


@pytest.fixture(autouse=True)
def _clean_embeddings_between_tests():
    """Clear embeddings table before each test for isolation.

    Without this, RAG integration tests leak data into unit tests
    when running the full suite (session-scoped DuckDB).
    """
    yield
    try:
        from app.database import get_db
        db = get_db()
        db.execute("DELETE FROM embeddings")
        db.commit()
    except Exception:
        pass

