"""Tests for database profile switching (main ↔ test).

Validates:
  - Default profile is 'main'
  - Switching to 'test' changes the DB path
  - Switching back restores the original path
  - Data written to test DB does not appear in main DB
  - Invalid profiles are rejected
"""

import os
import sys
from pathlib import Path

import pytest

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _reset_db():
    """Ensure each test starts with a clean DB state."""
    from app.database import reset_connection
    from app.config import settings

    # Save originals
    original_profile = settings.DB_PROFILE
    original_override = settings._db_path_override

    # Clear _db_path_override so profile-based paths are tested
    settings._db_path_override = None

    yield

    # Restore originals and close connection
    settings.DB_PROFILE = original_profile
    settings._db_path_override = original_override
    reset_connection()


@pytest.fixture
def cleanup_test_db():
    """Remove the test DB file after the test."""
    from app.config import settings

    test_path = settings.DATA_DIR / "trading_bot_test.duckdb"
    yield test_path
    # Cleanup
    if test_path.exists():
        test_path.unlink()
    # Also remove WAL files
    for ext in [".wal", ".tmp"]:
        p = test_path.with_suffix(test_path.suffix + ext)
        if p.exists():
            p.unlink()


def test_default_profile_is_main():
    """Settings should default to 'main' profile."""
    from app.config import settings

    # Reset to default
    settings.DB_PROFILE = "main"
    assert settings.DB_PROFILE == "main"
    assert "trading_bot.duckdb" in str(settings.DB_PATH)
    assert "test" not in str(settings.DB_PATH)


def test_switch_to_test_profile(cleanup_test_db):
    """switch_db('test') should change the path to the test database."""
    from app.database import switch_db, get_current_profile

    result = switch_db("test")

    assert result["profile"] == "test"
    assert "trading_bot_test.duckdb" in result["db_path"]

    # get_current_profile should agree
    current = get_current_profile()
    assert current["profile"] == "test"
    assert "test" in current["db_path"]


def test_switch_back_to_main(cleanup_test_db):
    """Switching test → main should restore the original path."""
    from app.database import switch_db, get_current_profile

    # Go to test
    switch_db("test")
    assert get_current_profile()["profile"] == "test"

    # Go back to main
    result = switch_db("main")
    assert result["profile"] == "main"
    assert "trading_bot_test" not in result["db_path"]
    assert "trading_bot.duckdb" in result["db_path"]


def test_invalid_profile_raises():
    """Invalid profile names should raise ValueError."""
    from app.database import switch_db

    with pytest.raises(ValueError, match="Invalid DB profile"):
        switch_db("production")

    with pytest.raises(ValueError, match="Invalid DB profile"):
        switch_db("")


def test_data_isolation(cleanup_test_db):
    """Data written to the test DB should not appear in the main DB."""
    from app.database import switch_db, get_db, reset_connection

    # Write to test DB
    switch_db("test")
    db = get_db()
    db.execute(
        "INSERT INTO watchlist (ticker, source, status, bot_id) "
        "VALUES ('ISOLATION_TEST', 'test', 'active', 'default')"
    )
    db.commit()

    # Verify it's in the test DB
    row = db.execute(
        "SELECT ticker FROM watchlist WHERE ticker = 'ISOLATION_TEST'"
    ).fetchone()
    assert row is not None
    assert row[0] == "ISOLATION_TEST"

    # Switch to main
    switch_db("main")
    db = get_db()

    # Should NOT be in main DB
    row = db.execute(
        "SELECT ticker FROM watchlist WHERE ticker = 'ISOLATION_TEST'"
    ).fetchone()
    assert row is None, "Test data leaked into main database!"


def test_get_current_profile_reflects_config():
    """get_current_profile should reflect the current settings.DB_PROFILE."""
    from app.config import settings
    from app.database import get_current_profile

    settings.DB_PROFILE = "main"
    assert get_current_profile()["profile"] == "main"

    settings.DB_PROFILE = "test"
    assert get_current_profile()["profile"] == "test"

    # Reset
    settings.DB_PROFILE = "main"
