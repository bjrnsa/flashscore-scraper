"""Database management module for Flashscore scraping."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


class DatabaseManager:
    """Manages database connections and operations."""

    def __init__(self, db_path: Path | str):
        """Initialize the DatabaseManager.

        Parameters
        ----------
        db_path : Path | str
            Path to the SQLite database file
        """
        self.db_path = str(db_path)  # sqlite3.connect requires str
        self._connection = None
        self._init_database()

    def _init_database(self) -> None:
        """Initialize the database with required tables."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        with self.get_cursor() as cursor:
            # Set performance-related PRAGMAs
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA cache_size = -10000")  # 10MB cache
            cursor.execute("PRAGMA foreign_keys = ON")

            # Create sport_ids table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sport_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create scraped_seasons table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scraped_seasons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sport_id INTEGER NOT NULL,
                    league TEXT NOT NULL,
                    season INTEGER  NOT NULL,
                    is_complete BOOLEAN DEFAULT 0,
                    last_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sport_id) REFERENCES sport_ids (id),
                    UNIQUE(sport_id, league, season)
                )
            """)

            # Create match IDs table (generic for all sport_ids)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS flashscore_ids (
                    flashscore_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    country TEXT,
                    league TEXT,
                    season INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sport_id) REFERENCES sport_ids (id)
                )
            """)

            # Create indexes for flashscore_ids
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_flashscore_sport ON flashscore_ids(sport_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_flashscore_league ON flashscore_ids(league)"
            )

            # Create match data table (generic for all sport_ids)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_data (
                    flashscore_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    country TEXT,
                    league TEXT,
                    season INTEGER,
                    datetime TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    home_goals INTEGER,
                    away_goals INTEGER,
                    result INTEGER,
                    additional_data JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES flashscore_ids (flashscore_id),
                    FOREIGN KEY (sport_id) REFERENCES sport_ids (id)
                )
            """)

            # Create indexes for match_data
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_match_sport ON match_data(sport_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_match_datetime ON match_data(datetime)"
            )

            # Create bookmaker_ids table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookmaker_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create odds_data table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS odds_data (
                    id INTEGER PRIMARY KEY,
                    flashscore_id TEXT,
                    sport_id INTEGER NOT NULL,
                    bookmaker_id INTEGER NOT NULL,
                    home_odds REAL,
                    draw_odds REAL,
                    away_odds REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES flashscore_ids (flashscore_id),
                    FOREIGN KEY (sport_id) REFERENCES sport_ids (id),
                    FOREIGN KEY (bookmaker_id) REFERENCES bookmaker_ids (id),
                    UNIQUE(flashscore_id, bookmaker_id)
                )
            """)

            # Create indexes for odds_data
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_odds_flashscore ON odds_data(flashscore_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_odds_bookmaker ON odds_data(bookmaker_id)"
            )

            # Create upcoming_fixtures table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS upcoming_fixtures (
                    flashscore_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    country TEXT,
                    league TEXT,
                    season INTEGER,
                    datetime TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES flashscore_ids (flashscore_id),
                    FOREIGN KEY (sport_id) REFERENCES sport_ids (id)
                )
            """)

            # Create indexes for upcoming_fixtures
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_upcoming_datetime ON upcoming_fixtures(datetime)"
            )

    def register_sport(self, sport_name: str) -> int:
        """Register a new sport in the database.

        Parameters
        ----------
        sport_name : str
            Name of the sport to register

        Returns:
        -------
        int
            ID of the registered sport
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO sport_ids (name)
                VALUES (?)
                """,
                (sport_name.lower(),),
            )

            cursor.execute(
                "SELECT id FROM sport_ids WHERE name = ?", (sport_name.lower(),)
            )
            return cursor.fetchone()[0]

    def register_bookmaker(self, bookmaker_name: str) -> int:
        """Register a new bookmaker in the database.

        Parameters
        ----------
        bookmaker_name : str
            Name of the bookmaker to register

        Returns:
        -------
        int
            ID of the registered bookmaker
        """
        with self.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO bookmaker_ids (name)
                VALUES (?)
                """,
                (bookmaker_name,),
            )

            cursor.execute(
                "SELECT id FROM bookmaker_ids WHERE name = ?", (bookmaker_name,)
            )
            return cursor.fetchone()[0]

    def is_connection_valid(self) -> bool:
        """Check if the current database connection is valid.

        Returns:
        -------
        bool
            True if connection is valid, False otherwise
        """
        if not self._connection:
            return False
        try:
            self._connection.execute("SELECT 1")
            return True
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return False

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a database connection with optimized settings.

        Returns:
        -------
        sqlite3.Connection
            Optimized database connection
        """
        if not self.is_connection_valid():
            if self._connection:
                self._connection.close()
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            self._connection = conn
        return self._connection

    @contextmanager
    def get_cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Create and manage a database cursor with retry logic.

        Yields:
        ------
        Generator[sqlite3.Cursor, None, None]
            A database cursor within a transaction

        Raises:
        ------
        sqlite3.OperationalError
            If database is locked after max retries
        sqlite3.Error
            For other SQLite related errors
        """
        max_retries = 3
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                yield cursor
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if attempt == max_retries - 1:
                    raise
                import time

                time.sleep(retry_delay * (attempt + 1))
                if "database is locked" in str(e):
                    if self._connection:
                        self._connection.close()
                        self._connection = None
            except Exception as e:
                if self._connection:
                    self._connection.close()
                    self._connection = None
                raise e  # Re-raise the original exception for better debugging

    def optimize_database(self) -> None:
        """Optimize the database by running VACUUM and analyzing indexes."""
        with self.get_cursor() as cursor:
            cursor.execute("VACUUM")
            cursor.execute("ANALYZE")

    def clear_table(self, table_name: str) -> None:
        """Clear all data from a specified table.

        Parameters
        ----------
        table_name : str
            The name of the table to clear

        Raises:
        ------
        ValueError
            If the specified table name is not allowed
        """
        allowed_tables = {
            "match_data",
            "odds_data",
            "flashscore_ids",
            "upcoming_fixtures",
        }
        if table_name not in allowed_tables:
            raise ValueError(
                f"Table name '{table_name}' is not allowed. Allowed tables are: {sorted(allowed_tables)}"
            )

        with self.get_cursor() as cursor:
            cursor.execute(f"DELETE FROM {table_name}")
            print(f"Table '{table_name}' has been cleared.")

    def drop_table(self, table_name: str) -> None:
        """Drop a specified table.

        Parameters
        ----------
        table_name : str
            The name of the table to drop

        Raises:
        ------
        ValueError
            If the specified table name is not allowed
        """
        allowed_tables = {
            "match_data",
            "odds_data",
            "flashscore_ids",
            "upcoming_fixtures",
        }
        if table_name not in allowed_tables:
            raise ValueError(
                f"Table name '{table_name}' is not allowed. Allowed tables are: {sorted(allowed_tables)}"
            )

        with self.get_cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
            print(f"Table '{table_name}' has been dropped.")

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None


if __name__ == "__main__":
    db_manager = DatabaseManager(db_path="database/database.db")
