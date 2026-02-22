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
