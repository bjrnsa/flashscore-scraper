"""Scrapes betting odds from OddsPortal.com using patterns from odds_data_scraper.py."""

import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from bs4 import BeautifulSoup
from ratelimit import limits, sleep_and_retry
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.scrapers.base import BaseScraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class OddsPortalScraper(BaseScraper):
    """Main scraper class following odds_data_scraper patterns."""

    BASE_URL = "http://www.oddsportal.com"
    DEFAULT_BATCH_SIZE = 20
    MAX_REQUESTS_PER_MINUTE = 30
    ONE_MINUTE = 60  # Changed from REQUEST_INTERVAL to match OddsDataScraper

    # Timeout constants
    PAGE_LOAD_TIMEOUT = 30
    ELEMENT_TIMEOUT = 10

    def __init__(self, db_path: Path | str = "database/database.db"):
        """Initialize the OddsPortalScraper with database path."""
        super().__init__(db_path)
        self.db_manager = self.get_database()
        # Ensure database directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.current_batch = []

    def _fetch_pending_matches(
        self, flashscore_ids: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """Fetch matches needing odds data from match_data table.

        Parameters
        ----------
        flashscore_ids : Optional[List[str]], optional
            List of specific match IDs to check, by default None

        Returns:
        -------
        pd.DataFrame
            DataFrame containing pending matches
        """
        # Base query to find matches without odds data
        query = """
            SELECT DISTINCT
                m.flashscore_id,
                s.name as sport_name,
                s.id as sport_id,
                CASE
                    WHEN f.flashscore_id IS NOT NULL THEN 'fixture'
                    ELSE 'completed'
                END as match_type,
                f.home_team,
                f.away_team,
                f.country,
                f.league
            FROM flashscore_ids m
            JOIN sport_ids s ON m.sport_id = s.id
            -- Check for completed matches only
            LEFT JOIN match_data md ON m.flashscore_id = md.flashscore_id
            LEFT JOIN odds_data o ON m.flashscore_id = o.flashscore_id
            LEFT JOIN upcoming_fixtures f ON m.flashscore_id = f.flashscore_id
            WHERE o.flashscore_id IS NULL
			AND DATE(f.datetime) < date('now','+3 day')
            -- AND md.flashscore_id IS NOT NULL  --  include completed matches
            -- AND f.flashscore_id IS NOT NULL         -- include upcoming_fixtures

        """

        # Add match ID filter if specified
        if flashscore_ids:
            if not isinstance(flashscore_ids, list):
                raise ValueError("flashscore_ids must be a list of strings")
            if not all(isinstance(id_, str) for id_ in flashscore_ids):
                raise ValueError("All match IDs must be strings")

            if len(flashscore_ids) == 1:
                query += " AND m.flashscore_id = ?"
                params = (flashscore_ids[0],)
            else:
                query += " AND m.flashscore_id IN ({})".format(
                    ",".join("?" * len(flashscore_ids))
                )
                params = tuple(flashscore_ids)
        else:
            params = ()

        with self.db_manager.get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def _generate_match_url(
        self,
        home_team: str,
        away_team: str,
        country: str,
        league: str,
        flashscore_id: str,
    ) -> str:
        """Generate OddsPortal URL from match data.

        Parameters
        ----------
        home_team : str
            Home team name
        away_team : str
            Away team name
        country : str
            Country name
        league : str
            League name
        flashscore_id : str
            Flashscore match ID

        Returns:
        -------
        str
            Generated URL
        """
        home = (
            home_team.replace(".", "")
            .replace("/", "-")
            .replace("W", "")
            .replace(" ", "")
        )
        away = (
            away_team.replace(".", "")
            .replace("/", "-")
            .replace("W", "")
            .replace(" ", "")
        )
        url = f"{self.BASE_URL}/handball/{country}/{league}/{home}-{away}"
        url = url.lower().replace(" ", "-")
        url = url + f"-{flashscore_id}/"
        return url

    def _accept_cookies(self, driver):
        """Handle cookie consent.

        Parameters
        ----------
        driver
            Selenium WebDriver instance
        """
        try:
            WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
            ).click()
            logger.debug("Accepted cookies")
        except Exception:
            logger.debug("No cookie banner found")

    def _parse_odds_page(self, driver) -> Dict[str, List[float]]:
        soup = BeautifulSoup(driver.page_source, "html.parser")
        elements = soup.find_all(
            "a", class_="min-mt:!flex text-color-black hidden underline"
        )
        no_odds_message = soup.find("div", string="No odds available for this match")
        if no_odds_message:
            return no_odds_message.text.strip()

        if elements:
            bookmaker_odds = {}

            for element in elements:
                bookmaker_name = element["href"].split("/")[2]
                odds_value = element.text.strip()

                try:
                    odds_value = float(odds_value)
                except ValueError:
                    continue

                if bookmaker_name not in bookmaker_odds:
                    bookmaker_odds[bookmaker_name] = []

                bookmaker_odds[bookmaker_name].append(odds_value)

            bookmaker_odds = {k: v for k, v in bookmaker_odds.items() if len(v) == 3}

        else:
            bookmaker_ids = [
                elem.text.strip().replace(" ", "-").lower()
                for elem in soup.find_all("p", class_="height-content pl-4")
            ]
            n_bookmaker_ids = len(bookmaker_ids)
            elements = soup.find_all("p", class_="height-content")[
                4 : 4 * n_bookmaker_ids + 4
            ]
            elements = [
                elements[i + j]
                for i in range(1, len(elements), 4)
                for j in range(3)
                if i + j < len(elements)
            ]
            all_odds = []
            for elem in elements:
                try:
                    all_odds.append(float(elem.text.strip()))
                except ValueError:
                    all_odds.append(1.0)
            bookmaker_odds = {
                bookmaker_ids[i]: all_odds[i * 3 : (i + 1) * 3]
                for i in range(n_bookmaker_ids)
            }
        return bookmaker_odds

    @sleep_and_retry
    @limits(calls=MAX_REQUESTS_PER_MINUTE, period=ONE_MINUTE)
    def _process_single_match(
        self, match: Dict, browser: BrowserManager
    ) -> Optional[Dict]:
        """Process individual match with rate limiting.

        Parameters
        ----------
        match : Dict
            Match data dictionary
        browser : BrowserManager
            Browser manager instance

        Returns:
        -------
        Optional[Dict]
            Processed match data if successful
        """
        try:
            url = self._generate_match_url(
                match["home_team"],
                match["away_team"],
                match["country"],
                match["league"],
                match["flashscore_id"],
            )
            logger.info(f"Processing {url}")
            max_retries = 10

            with browser.get_driver(url) as driver:
                odds_data = []
                n_retries = 0
                while not odds_data:
                    n_retries += 1
                    odds_data = self._parse_odds_page(driver)
                    if n_retries >= max_retries:
                        return None

                if isinstance(odds_data, str):
                    return None

            return {
                "flashscore_id": match["flashscore_id"],
                "sport_id": match["sport_id"],
                "bookmaker_odds": odds_data,
            }

        except WebDriverException as e:
            logger.error(f"Browser error processing {match['flashscore_id']}: {str(e)}")
            return None

    def _store_batch(self) -> bool:
        """Store collected odds in database.

        Returns:
        -------
        bool
            True if successful, False otherwise
        """
        if not self.current_batch:
            return True

        records = []
        with self.db_manager.get_cursor() as cursor:
            for entry in self.current_batch:
                for bookmaker, odds in entry["bookmaker_odds"].items():
                    try:
                        # Handle bookmaker management
                        cursor.execute(
                            "INSERT OR IGNORE INTO bookmaker_ids (name) VALUES (?)",
                            (bookmaker,),
                        )
                        cursor.execute(
                            "SELECT id FROM bookmaker_ids WHERE name = ?", (bookmaker,)
                        )
                        bookmaker_id = cursor.fetchone()[0]

                        records.append(
                            (
                                entry["flashscore_id"],
                                entry["sport_id"],
                                bookmaker_id,
                                odds[0],  # home
                                odds[1] if len(odds) > 1 else None,  # draw
                                odds[-1],  # away
                            )
                        )
                    except Exception as e:
                        logger.error(
                            f"Error preparing record for {bookmaker}: {str(e)}"
                        )
                        continue

        if not records:
            logger.error("No valid records to store")
            return False

        query = """
            INSERT OR REPLACE INTO odds_data
            (flashscore_id, sport_id, bookmaker_id, home_odds, draw_odds, away_odds)
            VALUES (?, ?, ?, ?, ?, ?)
        """

        success = self.execute_query(query, records)
        if success:
            logger.info(f"Stored {len(records)} odds records")
            self.current_batch = []
        return success

    def scrape(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
        flashscore_ids: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, int]]:
        """Main scraping method.

        Parameters
        ----------
        batch_size : int, optional
            Number of matches to process before storing, by default DEFAULT_BATCH_SIZE
        headless : bool, optional
            Whether to run browser headless, by default True
        flashscore_ids : Optional[List[str]], optional
            Specific match IDs to process, by default None

        Returns:
        -------
        Dict[str, Dict[str, int]]
            Statistics of processed matches by sport and type
        """
        try:
            browser = self.get_browser(headless)
            results: Dict[str, Dict[str, int]] = {}

            # Get pending matches
            sport_matches = self._fetch_pending_matches(flashscore_ids)
            if not sport_matches:
                logger.info("No matches requiring odds collection")
                return results

            # Process each sport's matches
            for sport_match in tqdm(sport_matches, desc="Processing odds"):
                sport_match = dict(sport_match)
                logger.info(f"Processing {sport_match['sport_name']} odds...")
                processed_matches = 0
                sport_results = {"upcoming_fixtures": 0, "completed": 0}

                try:
                    if result := self._process_single_match(sport_match, browser):
                        self.current_batch.append(result)
                        processed_matches += 1
                        # sport_results[sport_match["match_type"]] += 1

                        if len(self.current_batch) >= batch_size:
                            if self._store_batch():
                                processed_matches = 0
                            else:
                                logger.error("Failed to store batch")

                except Exception as e:
                    logger.error(
                        f"Failed to process match {sport_match['flashscore_id']}: {str(e)}"
                    )
            # Store remaining matches
            if self.current_batch:
                if not self._store_batch():
                    logger.error("Failed to store final batch")

            results[sport_match["sport_name"]] = sport_results
            logger.info(
                f"Completed {sport_match['sport_name']}: processed {sport_results['upcoming_fixtures']} upcoming_fixtures "
                f"and {sport_results['completed']} completed matches"
            )

            return results

        except Exception as e:
            logger.error(f"Scraping failed: {str(e)}")
            return results


if __name__ == "__main__":
    scraper = OddsPortalScraper()
    scraper.scrape(headless=True)
