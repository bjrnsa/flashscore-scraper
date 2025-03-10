"""Database management module for Flashscore scraping."""

import json
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

            # Create sports table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create match IDs table (generic for all sports)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_ids (
                    match_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    country TEXT,
                    league TEXT,
                    season TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (sport_id) REFERENCES sports (id)
                )
            """)

            # Create match data table (generic for all sports)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS match_data (
                    flashscore_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    country TEXT,
                    league TEXT,
                    season TEXT,
                    match_info TEXT,
                    datetime TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    home_score INTEGER,
                    away_score INTEGER,
                    result INTEGER,
                    additional_data JSON,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES match_ids (match_id),
                    FOREIGN KEY (sport_id) REFERENCES sports (id)
                )
            """)

            # Create odds data table (generic for all sports)
            # Draw odds can be null
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS odds_data (
                    id INTEGER PRIMARY KEY,
                    flashscore_id TEXT,
                    sport_id INTEGER NOT NULL,
                    bookmaker TEXT,
                    home_odds REAL,
                    draw_odds REAL,
                    away_odds REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES match_ids (match_id),
                    FOREIGN KEY (sport_id) REFERENCES sports (id),
                    UNIQUE(flashscore_id, bookmaker)
                )
            """)

            # Create fixtures table for upcoming matches
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fixtures (
                    flashscore_id TEXT PRIMARY KEY,
                    sport_id INTEGER NOT NULL,
                    country TEXT,
                    league TEXT,
                    season TEXT,
                    match_info TEXT,
                    datetime TEXT,
                    home_team TEXT,
                    away_team TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (flashscore_id) REFERENCES match_ids (match_id),
                    FOREIGN KEY (sport_id) REFERENCES sports (id)
                )
            """)

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
                INSERT OR IGNORE INTO sports (name)
                VALUES (?)
            """,
                (sport_name.lower(),),
            )

            cursor.execute(
                "SELECT id FROM sports WHERE name = ?", (sport_name.lower(),)
            )
            return cursor.fetchone()[0]

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a database connection with optimized settings.

        Returns:
        -------
        sqlite3.Connection
            Optimized database connection
        """
        if self._connection is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -10000")  # 10MB cache
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
            except Exception:
                if self._connection:
                    self._connection.close()
                    self._connection = None
                raise

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def clear_table(self, table_name: str) -> None:
        """Clears all data from a specified table.

        Parameters
        ----------
        table_name : str
            The name of the table to clear (e.g., 'match_data', 'odds_data').

        Raises:
        ------
        ValueError
            If the specified table name is not allowed.

        """
        allowed_tables = {"match_data", "odds_data"}
        if table_name not in allowed_tables:
            raise ValueError(
                f"Table name '{table_name}' is not allowed. Allowed tables are: {sorted(allowed_tables)}"
            )

        with self.get_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {table_name}"
            )  # Safe after allowlist validation
            print(f"Table '{table_name}' has been cleared.")

    def override_match_result(self, flashscore_id: str, match_data: dict) -> None:
        """Override match result for exceptional cases.

        This method allows manual insertion or update of match results that cannot be
        scraped normally, such as matches decided by administrative decisions.

        Parameters
        ----------
        flashscore_id : str
            The FlashScore ID of the match
        match_data : dict
            Dictionary containing match data with keys matching match_data table columns:
            - sport_id (required): Integer ID of the sport
            - country (required): Country where the match was played
            - league (required): League/competition name
            - season (required): Season identifier (e.g., "2023/2024")
            - match_info (required): Additional match information
            - datetime (required): Match date and time
            - home_team (required): Home team name
            - away_team (required): Away team name
            - home_score (required): Home team score
            - away_score (required): Away team score
            - result (required): Match result (-1: away win, 0: draw, 1: home win)
            - additional_data (optional): JSON-serializable dict with additional details


        Raises:
        ------
        ValueError
            If required fields are missing or invalid
        sqlite3.Error
            If database operation fails
        """
        required_fields = {
            "sport_id",
            "country",
            "league",
            "season",
            "match_info",
            "datetime",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "result",
        }

        # Validate required fields
        missing_fields = required_fields - set(match_data.keys())
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        # Validate result value
        if match_data["result"] not in {-1, 0, 1}:
            raise ValueError("Result must be -1 (away win), 0 (draw), or 1 (home win)")

        try:
            with self.get_cursor() as cursor:
                # First check if match_id exists in match_ids table
                cursor.execute(
                    "SELECT 1 FROM match_ids WHERE match_id = ?", (flashscore_id,)
                )
                if not cursor.fetchone():
                    raise ValueError(
                        f"Match ID {flashscore_id} not found in match_ids table"
                    )

                # Convert additional_data to JSON if present
                additional_data = (
                    json.dumps(match_data.get("additional_data"))
                    if "additional_data" in match_data
                    else None
                )

                # Insert or update match data with override flag
                cursor.execute(
                    """
                    INSERT INTO match_data (
                        flashscore_id, sport_id, country, league, season,
                        match_info, datetime, home_team, away_team,
                        home_score, away_score, result,
                        additional_data
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(flashscore_id) DO UPDATE SET
                        sport_id = excluded.sport_id,
                        country = excluded.country,
                        league = excluded.league,
                        season = excluded.season,
                        match_info = excluded.match_info,
                        datetime = excluded.datetime,
                        home_team = excluded.home_team,
                        away_team = excluded.away_team,
                        home_score = excluded.home_score,
                        away_score = excluded.away_score,
                        result = excluded.result,
                        additional_data = excluded.additional_data
                        """,
                    (
                        flashscore_id,
                        match_data["sport_id"],
                        match_data["country"],
                        match_data["league"],
                        match_data["season"],
                        match_data["match_info"],
                        match_data["datetime"],
                        match_data["home_team"],
                        match_data["away_team"],
                        match_data["home_score"],
                        match_data["away_score"],
                        match_data["result"],
                        additional_data,
                    ),
                )

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Database error during match override: {str(e)}")
        except Exception as e:
            raise ValueError(f"Error during match override: {str(e)}")

    def drop_table(self, table_name: str) -> None:
        """Drops a specified table.

        Parameters
        ----------
        table_name : str
            The name of the table to drop (e.g., 'match_data', 'odds_data').

        Raises:
        ------
        ValueError
            If the specified table name is not allowed.

        """
        allowed_tables = ["match_data", "odds_data"]
        if table_name not in allowed_tables:
            raise ValueError(
                f"Table name '{table_name}' is not allowed. Allowed tables are: {allowed_tables}"
            )

        with self.get_cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
            print(f"Table '{table_name}' has been dropped.")


if __name__ == "__main__":
    db_manager = DatabaseManager(db_path="database/database.db")
    db_manager.override_match_result(
        "hMmAicbk",
        {
            "sport_id": 1,
            "country": "DENMARK",
            "league": "Herre Handbold Ligaen",
            "season": "2024/2025",
            "match_info": "Round 10",
            "datetime": "08.02.2025 14:30",
            "home_team": "Grindsted",
            "away_team": "Ribe-Esbjerg",
            "home_score": 27,
            "away_score": 27,
            "result": 1,
            "additional_data": {
                "override_reason": "Administrative decision - illegal player"
            },
        },
    )
