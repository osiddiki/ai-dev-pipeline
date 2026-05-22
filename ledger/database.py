import sqlite3
import aiosqlite
import structlog
from pathlib import Path

logger = structlog.get_logger()

DB_PATH = Path(__file__).parent.parent / "trust_ledger.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

async def init_db():
    """Initialize the SQLite database with the schema."""
    logger.info("Initializing database", db_path=str(DB_PATH))
    async with aiosqlite.connect(DB_PATH) as db:
        with open(SCHEMA_PATH, "r") as f:
            schema_script = f.read()
        await db.executescript(schema_script)
        await db.commit()
    logger.info("Database initialized successfully")

async def get_db():
    """Get a database connection."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

def init_db_sync():
    """Synchronous init for startup script."""
    logger.info("Initializing database synchronously", db_path=str(DB_PATH))
    with sqlite3.connect(DB_PATH) as db:
        with open(SCHEMA_PATH, "r") as f:
            db.executescript(f.read())
        db.commit()
    logger.info("Database initialized synchronously")

if __name__ == "__main__":
    init_db_sync()
