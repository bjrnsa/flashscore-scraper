"""Module for scraping betting odds data from FlashScore."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag
from pydantic import ValidationError
from ratelimit import limits, sleep_and_retry
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.exceptions import ParsingException, ValidationException
from flashscore_scraper.models.base import MatchOdds
from flashscore_scraper.scrapers.base import BaseScraper

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class OddsDataScraper(BaseScraper):
    """Scrapes and stores betting odds data from FlashScore matches.

    This scraper is responsible for collecting betting odds from various bookmakers
    for matches stored in the database. It handles rate limiting, data validation,
    and batch processing of odds data.

    Attributes:
    ----------
    MATCH_URL : str
        Base URL for individual match pages
    DEFAULT_BATCH_SIZE : int
        Default number of matches to process in a single batch
    """

    # Override only the specific URL needed for matches
    MATCH_URL = "https://www.flashscore.com/match"
    DEFAULT_BATCH_SIZE = 10
    MAX_REQUESTS_PER_MINUTE = 60
    ONE_MINUTE = 60

    def __init__(
        self,
        db_path: Path | str = "database/database.db",
    ):
        """Initialize the OddsDataScraper with database path.

        Parameters
        ----------
        db_path : Path | str, optional
            Path to the SQLite database file where match and odds data is stored.
            Parent directories will be created if they don't exist.
            Defaults to "database/database.db".
        """
        super().__init__(db_path)
        self.db_manager = self.get_database()
        # Ensure database directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _fetch_pending_matches(
        self, match_ids: Optional[List[str]] = None
    ) -> List[Tuple[str, str, int]]:
        """Get matches that need odds data collection.

        Queries the database for matches that don't have associated odds data yet.
        Can be filtered to specific match IDs if provided.

        Parameters
        ----------
        match_ids : Optional[List[str]], optional
            List of specific match IDs to check for pending odds data.
            If None, checks all matches in the database.
            Defaults to None.

        Returns:
        -------
        List[Tuple[str, str, int]]
            List of tuples containing:
            - match_id (str): The FlashScore match identifier
            - sport_name (str): Name of the sport
            - sport_id (int): Database ID of the sport
            Empty list if no pending matches are found.

        Notes:
        -----
        The query joins the match_ids and sports tables, then left joins with
        odds_data to find matches without odds information.
        """
        try:
            # Base query to find matches without odds data
            query = """
                SELECT
                    m.match_id,
                    s.name as sport_name,
                    s.id as sport_id
                FROM match_ids m
                JOIN sports s ON m.sport_id = s.id
                LEFT JOIN odds_data o ON m.match_id = o.flashscore_id
                WHERE o.flashscore_id IS NULL
            """

            # Add match ID filter if specified
            if match_ids:
                if not isinstance(match_ids, list):
                    raise ValueError("match_ids must be a list of strings")
                if not all(isinstance(id_, str) for id_ in match_ids):
                    raise ValueError("All match IDs must be strings")
                # Handle single ID case properly
                if len(match_ids) == 1:
                    query += " AND m.match_id = ?"
                    params = (match_ids[0],)
                else:
                    query += " AND m.match_id IN ({})".format(
                        ",".join("?" * len(match_ids))
                    )
                    params = tuple(match_ids)
            else:
                params = ()

            with self.db_manager.get_cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

        except Exception as e:
            logger.error(f"Failed to fetch pending matches: {str(e)}")
            return []

    def _parse_odds_row(
        self, odds_row: Tag, match_id: str, sport_id: int
    ) -> List[MatchOdds]:
        """Extract and validate bookmaker odds from odds row element.

        Parameters
        ----------
        odds_row : Tag
            BeautifulSoup Tag object containing the odds row.
        match_id : str
            The FlashScore match ID.
        sport_id : int
            The sport ID.

        Returns:
        -------
        List[MatchOdds]
            List of validated match odds objects.

        Raises:
        ------
        ParsingException
            If there are issues parsing the odds data.
        ValidationException
            If the odds data fails validation.
        """
        results = []
        try:
            odds_contents = odds_row.find_all(class_="oddsRowContent")
            if not odds_contents:
                raise ParsingException("No odds content found")

            for bookmaker in odds_contents:
                try:
                    if not isinstance(bookmaker, Tag):
                        continue

                    img = bookmaker.find("img")
                    if not isinstance(img, Tag) or "alt" not in img.attrs:
                        continue

                    name = str(img["alt"])  # Convert to string explicitly
                    odds_elements = bookmaker.find_all(class_="oddsValueInner")
                    odds_values = [
                        self._parse_odd(el.text.strip()) for el in odds_elements
                    ]

                    # Create and validate odds data
                    if len(odds_elements) == 3 and all(odds_values):
                        odds = MatchOdds(
                            flashscore_id=match_id,
                            sport_id=sport_id,
                            bookmaker=name,
                            home_odds=odds_values[0],
                            draw_odds=odds_values[1],
                            away_odds=odds_values[2],
                        )
                        results.append(odds)
                    elif len(odds_elements) == 2 and all(odds_values):
                        odds = MatchOdds(
                            flashscore_id=match_id,
                            sport_id=sport_id,
                            bookmaker=name,
                            home_odds=odds_values[0],
                            draw_odds=0.0,
                            away_odds=odds_values[1],
                        )
                        results.append(odds)

                except ValidationError as e:
                    logger.warning(f"Invalid odds data from {name}: {str(e)}")
                except (AttributeError, KeyError) as e:
                    logger.warning(f"Failed to parse odds from {name}: {str(e)}")

            return results

        except Exception as e:
            raise ParsingException(f"Failed to parse odds row: {str(e)}") from e

    def _parse_odd(self, value: str) -> float:
        """Safely convert odd value to float.

        Parameters
        ----------
        value : str
            The odd value as a string.

        Returns:
        -------
        float
            The odd value as a float, or 0.0 if invalid.
        """
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @sleep_and_retry
    @limits(calls=MAX_REQUESTS_PER_MINUTE, period=ONE_MINUTE)
    def _process_match_odds(
        self, match_id: str, sport_id: int, sport_name: str, browser: BrowserManager
    ) -> List[MatchOdds]:
        """Scrape and parse odds data for a single match with rate limiting.

        This method handles the entire process of loading a match page, waiting for
        odds data to become available, and parsing the odds from various bookmakers.
        It includes rate limiting to prevent overloading the server.

        Parameters
        ----------
        match_id : str
            The FlashScore match ID to process.
        sport_id : int
            The database ID of the sport.
        sport_name : str
            The name of the sport (for logging purposes).
        browser : BrowserManager
            The browser manager instance to use for web scraping.

        Returns:
        -------
        List[MatchOdds]
            List of validated match odds objects from various bookmakers.
            Returns an empty list if no odds are available or if errors occur.

        Notes:
        -----
        The method uses rate limiting decorators to prevent exceeding
        server request limits. Failed requests or parsing errors are logged
        but don't stop the overall process.
        """
        url = f"{self.MATCH_URL}/{match_id}/#/match-summary"
        results = []

        try:
            with browser.get_driver(url) as driver:
                logger.debug(f"Loading odds for match {match_id} ({sport_name})")

                # Wait for odds row to be present
                try:
                    WebDriverWait(driver, self.TIMEOUT).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "oddsRow"))
                    )
                except TimeoutException:
                    logger.info(
                        f"No odds data available for match {match_id} ({sport_name})"
                    )
                    return results

                # Parse the loaded page
                soup = BeautifulSoup(driver.page_source, "html.parser")
                odds_row = soup.find(class_="oddsRow")

                if not odds_row:
                    logger.warning(f"Odds row not found for match {match_id}")
                    return results

                if not isinstance(odds_row, Tag):
                    logger.warning(f"Invalid odds row type for match {match_id}")
                    return results

                try:
                    results = self._parse_odds_row(odds_row, match_id, sport_id)
                    if results:
                        logger.info(
                            f"Successfully parsed odds from {len(results)} bookmakers for match {match_id}"
                        )
                    else:
                        logger.warning(f"No valid odds found for match {match_id}")
                except (ParsingException, ValidationException) as e:
                    logger.error(
                        f"Failed to parse odds for match {match_id} ({sport_name}): {str(e)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Unexpected error processing match {match_id} ({sport_name}): {str(e)}"
                    )

        except Exception as e:
            logger.error(f"Failed to process match {match_id} ({sport_name}): {str(e)}")

        return results

    def scrape(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
        match_ids: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        """Orchestrate the odds scraping workflow with progress tracking.

        This method coordinates the entire scraping process, including:
        - Fetching pending matches from the database
        - Grouping matches by sport for efficient processing
        - Managing browser sessions and rate limiting
        - Batch processing odds data for database storage
        - Progress tracking and error handling

        Parameters
        ----------
        batch_size : int, optional
            Number of matches to process before storing to database.
            Smaller batches reduce memory usage but increase database writes.
            Defaults to DEFAULT_BATCH_SIZE (10).
        headless : bool, optional
            Whether to run the browser in headless mode. Set to False for
            debugging or visual verification. Defaults to True.
        match_ids : Optional[List[str]], optional
            Specific match IDs to scrape odds for. If None, processes all
            pending matches in the database. Defaults to None.

        Returns:
        -------
        Dict[str, int]
            Dictionary mapping sport names to number of matches processed.
            Example: {'football': 100, 'basketball': 50}
            Note: Counts include matches attempted, even if odds weren't found.

        Notes:
        -----
        The method uses batch processing to optimize database operations and
        includes progress bars for monitoring long-running scrapes. Failed
        matches are logged but don't stop the overall process.
        """
        try:
            browser = self.get_browser(headless)
            results = {}

            # Get pending matches
            matches = self._fetch_pending_matches(match_ids)
            if not matches:
                logger.info("No matches requiring odds collection")
                return results

            # Group matches by sport
            sport_matches: Dict[str, List[Tuple[str, int]]] = {}
            for match_id, sport_name, sport_id in matches:
                if sport_name not in sport_matches:
                    sport_matches[sport_name] = []
                sport_matches[sport_name].append((match_id, sport_id))

            # Process each sport's matches
            for sport_name, sport_data in sport_matches.items():
                logger.info(f"Processing {sport_name} odds...")
                # Track matches and their odds separately
                match_buffer: Dict[str, List[MatchOdds]] = {}
                processed_matches = 0

                with tqdm(
                    total=len(sport_data), desc=f"Scraping {sport_name} odds"
                ) as pbar:
                    for match_id, sport_id in sport_data:
                        try:
                            if odds_list := self._process_match_odds(
                                match_id, sport_id, sport_name, browser
                            ):
                                match_buffer[match_id] = odds_list
                                processed_matches += 1

                                # Store batch when we have enough unique matches
                                if processed_matches >= batch_size:
                                    # Flatten all odds for storage while maintaining match grouping
                                    batch_odds = [
                                        odds
                                        for match_odds in match_buffer.values()
                                        for odds in match_odds
                                    ]
                                    if self._store_batch(batch_odds):
                                        match_buffer.clear()
                                        processed_matches = 0
                                    else:
                                        logger.error(
                                            "Failed to store batch, retaining data"
                                        )

                        except Exception as e:
                            logger.error(
                                f"Failed to process match {match_id}: {str(e)}"
                            )
                        finally:
                            pbar.update(1)

                    # Store any remaining matches
                    if match_buffer:
                        batch_odds = [
                            odds
                            for match_odds in match_buffer.values()
                            for odds in match_odds
                        ]
                        if not self._store_batch(batch_odds):
                            logger.error("Failed to store final batch")

                results[sport_name] = len(sport_data)
                logger.info(
                    f"Completed {sport_name}: processed {len(sport_data)} matches with odds from {len(batch_odds)} bookmakers"
                )

            return results

        except Exception as e:
            logger.error(f"Scraping failed: {str(e)}")
            return results

    def _store_batch(self, odds_list: List[MatchOdds]) -> bool:
        """Store batch of odds records.

        Parameters
        ----------
        odds_list : List[MatchOdds]
            List of validated match odds objects.

        Returns:
        -------
        bool
            True if successful, False otherwise
        """
        try:
            # Prepare records from validated models
            records = [
                (
                    odds.flashscore_id,
                    odds.sport_id,
                    odds.bookmaker,
                    odds.home_odds,
                    odds.draw_odds,
                    odds.away_odds,
                )
                for odds in odds_list
            ]

            if not records:
                logger.error("No valid records to store")
                return False

            # Construct and execute insert query
            query = """
                INSERT INTO odds_data (
                    flashscore_id, sport_id, bookmaker,
                    home_odds, draw_odds, away_odds
                ) VALUES (?, ?, ?, ?, ?, ?)
            """

            success = self.execute_query(query, records)
            if success:
                logger.info(f"Successfully stored {len(records)} odds records")
            return success

        except Exception as e:
            logger.error(f"Failed to store odds batch: {str(e)}")
            return False


if __name__ == "__main__":
    scraper = OddsDataScraper(db_path="database/database.db")
    results = scraper.scrape(headless=True, batch_size=5)

    for sport, count in results.items():
        print(f"Scraped {count} matches for {sport}")
