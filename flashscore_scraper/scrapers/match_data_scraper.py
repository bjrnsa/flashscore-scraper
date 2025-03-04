"""Module for scraping detailed match data from FlashScore."""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.exceptions import ParsingException, ValidationException
from flashscore_scraper.models.base import MatchResult
from flashscore_scraper.scrapers.base import BaseScraper

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class MatchDataScraper(BaseScraper):
    """Scrapes detailed match data and stores it in a structured format."""

    BASE_URL = "https://www.flashscore.com/match/"
    DEFAULT_BATCH_SIZE = 100
    TIMEOUT = 5
    CLICK_DELAY = 0.5

    # Rate limiting constants
    MAX_REQUESTS_PER_MINUTE = 120
    ONE_MINUTE = 60
    MAX_RETRIES = 20

    def __init__(
        self,
        db_path: str = "database/database.db",
    ):
        """Initialize the MatchDataScraper."""
        super().__init__(db_path)
        self.db_manager = self.get_database()

    def _calculate_season(self, month: int, year: int) -> str:
        """Determine season string based on match date.

        Parameters
        ----------
        month : int
            The month of the match.
        year : int
            The year of the match.

        Returns:
        -------
        str
            The season string in the format "YYYY/YYYY".
        """
        return f"{year}/{year + 1}" if month >= 8 else f"{year - 1}/{year}"

    def _parse_datetime(self, dt_str: str) -> Tuple[str, int, int]:
        """Extract datetime components from raw string.

        Parameters
        ----------
        dt_str : str
            The datetime string to parse.

        Returns:
        -------
        Tuple[str, int, int]
            The original datetime string, month, and year.
        """
        date_parts = dt_str.split(".")
        _, month = map(int, date_parts[:2])
        year_time = date_parts[2].split()
        year = int(year_time[0])
        return dt_str, month, year

    def scrape(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
        matches: List[Any] = [],
    ) -> Dict[str, int]:
        """Main scraping workflow with progress tracking and optimized browser usage.

        Parameters
        ----------
        batch_size : int, optional
            Number of records to insert in each batch. Defaults to DEFAULT_BATCH_SIZE.
        headless : bool, optional
            Whether to run the browser in headless mode. Defaults to True.
        matches : List[Any], optional
            List of matches to process. If empty, fetches from database.

        Returns:
        -------
        Dict[str, int]
            Dictionary mapping sport names to number of matches scraped.
        """
        results = {}

        # Fetch unprocessed matches if none provided
        if not matches:
            logger.info("No matches passed, fetching from database")
            with self.db_manager.get_cursor() as cursor:
                cursor.execute("""
                    SELECT m.match_id, s.name as sport_name, s.id as sport_id
                    FROM match_ids m
                    JOIN sports s ON m.sport_id = s.id
                    LEFT JOIN match_data d ON m.match_id = d.flashscore_id
                    WHERE d.flashscore_id IS NULL
                    ORDER BY s.name, m.created_at
                """)
                matches = cursor.fetchall()

        if not matches:
            logger.info("No matches to process")
            return results

        # Group matches by sport for efficient processing
        sport_matches: Dict[str, List[Tuple[str, int]]] = {}
        for match_id, sport_name, sport_id in matches:
            sport_matches.setdefault(sport_name, []).append((match_id, sport_id))

        # Create single browser instance for all processing
        browser = self.get_browser(headless)

        try:
            for sport_name, sport_data in sport_matches.items():
                data_buffer = []

                success_count = 0
                with tqdm(
                    total=len(sport_data), desc=f"Scraping {sport_name} matches"
                ) as pbar:
                    for match_id, sport_id in sport_data:
                        try:
                            url = f"{self.BASE_URL}{match_id}/#/match"
                            match_data = self._process_match(
                                browser, url, match_id, sport_id, sport_name
                            )

                            if match_data:
                                data_buffer.append(match_data)
                                success_count += 1

                                # Store batch if buffer is full
                                if len(data_buffer) >= batch_size:
                                    if self._store_batch(data_buffer):
                                        data_buffer.clear()
                                    else:
                                        logger.error("Failed to store batch")
                                        data_buffer.clear()

                        except Exception as e:
                            logger.error(
                                f"Failed to process match {match_id}: {str(e)}"
                            )
                        finally:
                            pbar.update(1)

                    # Store any remaining matches
                    if data_buffer:
                        if self._store_batch(data_buffer):
                            logger.info(
                                f"Stored final batch of {len(data_buffer)} matches"
                            )
                        else:
                            logger.error("Failed to store final batch")

                results[sport_name] = success_count

        finally:
            browser.close()

        return results

    def _process_match(
        self,
        browser: BrowserManager,
        url: str,
        match_id: str,
        sport_id: int,
        sport_name: str,
    ) -> Optional[Dict[str, Any]]:
        """Process a single match with optimized browser usage.

        Parameters
        ----------
        browser : BrowserManager
            Browser manager instance
        url : str
            Match URL
        match_id : str
            Match ID
        sport_id : int
            Sport ID
        sport_name : str
            Sport name

        Returns:
        -------
        Optional[Dict[str, Any]]
            Match data if successful, None otherwise
        """
        with browser.get_driver(url) as driver:
            for _ in range(self.MAX_RETRIES):
                soup = self._scrape_site(driver)
                try:
                    details = self._match_results(soup, match_id, sport_id).model_dump()
                    additional = self._parse_additional_details(soup, sport_name)

                    if details and additional:
                        details.update(
                            {
                                "flashscore_id": match_id,
                                "sport_id": sport_id,
                                "additional": additional,
                            }
                        )
                        return details
                except (ParsingException, ValidationException) as e:
                    logger.warning(f"Retry needed for {match_id}: {str(e)}")
                    time.sleep(self.CLICK_DELAY)

            logger.error(f"Max retries exceeded for match {match_id}")
            return None

    def _scrape_site(self, driver: WebDriver) -> BeautifulSoup:
        """Load a page in the browser and return the parsed content.

        Parameters
        ----------
        driver : WebDriver
            The Selenium WebDriver instance.
        url : str
            The URL to load in the browser.

        Returns:
        -------
        BeautifulSoup
            The parsed HTML content of the page.
        """
        WebDriverWait(driver, self.TIMEOUT).until(
            EC.presence_of_element_located((By.CLASS_NAME, "tournamentHeader__country"))
        )
        return BeautifulSoup(driver.page_source, "html.parser")

    def _match_results(
        self, soup: BeautifulSoup, match_id: str, sport_id: int
    ) -> MatchResult:
        """Extract and validate match results from page content.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content
        match_id : str
            FlashScore match ID
        sport_id : int
            Sport ID from database

        Returns:
        -------
        MatchResult
            Validated match result data

        Raises:
        ------
        ParsingException
            If required elements cannot be found
        ValidationException
            If extracted data is invalid
        """
        try:
            # Extract tournament header
            header = soup.find("span", class_="tournamentHeader__country")
            if not header or not isinstance(header, Tag):
                raise ParsingException("Could not find tournament header")

            # Extract datetime
            dt_header = header.find_next("div", class_="duelParticipant__startTime")
            if not dt_header:
                raise ParsingException("Could not find start time")
            dt_str = dt_header.text
            dt_str, month, year = self._parse_datetime(dt_str)

            # Extract teams
            home_team = soup.select_one(
                ".duelParticipant__home .participant__participantName"
            )
            away_team = soup.select_one(
                ".duelParticipant__away .participant__participantName"
            )
            if not home_team or not away_team:
                raise ParsingException("Could not find team names")

            # Extract scores
            final_score_element = soup.select_one(".detailScore__wrapper")
            if not final_score_element:
                raise ParsingException("Could not find final score")

            try:
                home_score, away_score = map(
                    int, final_score_element.text.strip().split("-")
                )
            except (ValueError, IndexError) as e:
                raise ParsingException(f"Invalid score format: {str(e)}")

            # Calculate result
            result = (
                1 if home_score > away_score else -1 if home_score < away_score else 0
            )

            # Extract tournament info
            tournament_info_element = soup.select_one(".tournamentHeader__country")
            if not tournament_info_element:
                raise ParsingException("Could not find tournament header")

            tournament_info = tournament_info_element.text
            try:
                country = tournament_info.split(":")[0].strip()
                league = tournament_info.split(":")[1].strip().split("-")[0].strip()
                match_info = tournament_info.split(" - ")[-1].strip()
            except IndexError as e:
                raise ParsingException(f"Invalid tournament info format: {str(e)}")

            # Create and validate match result
            return MatchResult(
                country=country,
                league=league,
                season=self._calculate_season(month, year),
                match_info=match_info,
                datetime=dt_str,
                home_team=home_team.text.strip(),
                away_team=away_team.text.strip(),
                home_score=home_score,
                away_score=away_score,
                result=result,
                sport_id=sport_id,
                flashscore_id=match_id,
            )

        except ParsingException:
            raise
        except ValidationException:
            raise
        except Exception as e:
            raise ParsingException(f"Failed to parse match results: {str(e)}") from e

    def _parse_additional_details(
        self, soup: BeautifulSoup, sport: str
    ) -> Optional[Dict[str, int]]:
        """Parse additional match details based on sport type.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content
        sport : str
            Sport name

        Returns:
        -------
        Optional[Dict[str, int]]
            Sport-specific match details if available, None otherwise
        """
        try:
            parsers = {
                "handball": self._parse_handball_details,
                "volleyball": self._parse_volleyball_details,
                "football": self._parse_football_details,
            }
            parser = parsers.get(sport.lower())

            if not parser:
                logger.debug(f"No parser available for sport: {sport}")
                return None

            return parser(soup)
        except Exception as e:
            logger.error(f"Error parsing additional details for {sport}: {str(e)}")
            return None

    def _parse_football_details(self, soup: BeautifulSoup) -> Optional[Dict[str, int]]:
        """Parse football-specific match details.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content

        Returns:
        -------
        Optional[Dict[str, int]]
            Football match details if available, None otherwise
        """
        try:
            # Extract all period score elements
            match_parts = soup.find_all(
                class_="wcl-overline_rOFfd wcl-scores-overline-02_n9EXm"
            )

            if not match_parts:
                return None

            # Map period names to standardized keys
            period_mapping = {
                "1st Half": "first_half",
                "2nd Half": "second_half",
                "Extra Time": "extra_time",
                "Penalties": "penalties",
            }

            details: Dict[str, int] = {}
            for i, part in enumerate(match_parts):
                period_name = part.text.strip()
                if period_name in period_mapping:
                    try:
                        # Get the score element that follows the period name
                        score_text = match_parts[i + 1].text.strip()
                        home_score, away_score = map(int, score_text.split("-"))

                        key = period_mapping[period_name]
                        details[f"home_score_{key}"] = home_score
                        details[f"away_score_{key}"] = away_score
                    except (IndexError, ValueError, AttributeError):
                        logger.warning(f"Failed to parse score for {period_name}")
                        continue

            return details if details else None

        except Exception as e:
            logger.error(f"Error parsing football details: {str(e)}")
            return None

    def _get_match_parts(
        self, soup: BeautifulSoup
    ) -> Optional[Tuple[List[Tag], List[Tag]]]:
        """Extract match part elements for handball and volleyball matches.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content

        Returns:
        -------
        Optional[Tuple[List[Tag], List[Tag]]]
            Tuple of (home_parts, away_parts) if found, None otherwise
        """
        try:
            # Convert ResultSet to List[Tag] by filtering and converting
            home_parts = [
                tag
                for tag in soup.find_all(
                    "div",
                    class_=lambda c: self._class_filter(
                        c,
                        ["smh__part", "smh__home"],
                        ["smh__part--current", "smh__participantName"],
                    ),
                )
                if isinstance(tag, Tag)
            ]
            away_parts = [
                tag
                for tag in soup.find_all(
                    "div",
                    class_=lambda c: self._class_filter(
                        c,
                        ["smh__part", "smh__away"],
                        ["smh__part--current", "smh__participantName"],
                    ),
                )
                if isinstance(tag, Tag)
            ]

            if not home_parts or not away_parts:
                logger.debug("No match parts found")
                return None

            return home_parts, away_parts
        except Exception as e:
            logger.error(f"Error getting match parts: {str(e)}")
            return None

    def _parse_handball_details(self, soup: BeautifulSoup) -> Optional[Dict[str, int]]:
        """Parse handball-specific match details.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content

        Returns:
        -------
        Optional[Dict[str, int]]
            Handball match details if available, None otherwise
        """
        try:
            match_parts = self._get_match_parts(soup)
            if not match_parts:
                return None

            home_parts, away_parts = match_parts
            details: Dict[str, int] = {}

            # Map period indices to score keys
            period_mapping = {
                0: ("h1", "First half"),
                1: ("h2", "Second half"),
            }

            for i, (home_part, away_part) in enumerate(zip(home_parts, away_parts)):
                if i not in period_mapping:
                    continue

                try:
                    period_key, period_name = period_mapping[i]
                    home_value = int(home_part.get_text(strip=True))
                    away_value = int(away_part.get_text(strip=True))
                    details[f"home_score_{period_key}"] = home_value
                    details[f"away_score_{period_key}"] = away_value
                except (ValueError, AttributeError):
                    logger.warning(f"Failed to parse score for {period_name}")
                    continue

            return details if details else None

        except Exception as e:
            logger.error(f"Error parsing handball details: {str(e)}")
            return None

    def _parse_volleyball_details(
        self, soup: BeautifulSoup
    ) -> Optional[Dict[str, int]]:
        """Parse volleyball-specific match details.

        Parameters
        ----------
        soup : BeautifulSoup
            Parsed page content

        Returns:
        -------
        Optional[Dict[str, int]]
            Volleyball match details if available, None otherwise
        """
        match_parts = self._get_match_parts(soup)
        if not match_parts:
            return None

        home_parts, away_parts = match_parts
        details: Dict[str, int] = {}

        for i, (home_part, away_part) in enumerate(zip(home_parts, away_parts)):
            try:
                home_value = int(home_part.get_text(strip=True))
                away_value = int(away_part.get_text(strip=True))
                details[f"home_score_set_{i + 1}"] = home_value
                details[f"away_score_set_{i + 1}"] = away_value
            except (ValueError, AttributeError):
                continue

        return details if details else None

    def _store_batch(self, data: List[Dict[str, Any]]) -> bool:
        """Store batch of match records.

        Parameters
        ----------
        data : List[Dict[str, Any]]
            List of match data dictionaries to insert.

        Returns:
        -------
        bool
            True if successful, False otherwise

        Raises:
        ------
        ValidationException
            If any match data fails validation
        """
        fields = [
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
            "sport_id",
            "flashscore_id",
            "additional_data",
        ]

        try:
            if not data:
                logger.error("Empty data batch provided")
                return False

            # Prepare records with proper field ordering and JSON serialization
            records = []
            for match in data:
                try:
                    # Convert additional details to additional_data
                    if "additional" in match:
                        match["additional_data"] = json.dumps(match.pop("additional"))

                    # Create ordered values list based on fields
                    values = [match.get(field) for field in fields]
                    records.append(tuple(values))
                except (KeyError, TypeError, ValueError) as e:
                    logger.error(f"Invalid match data format: {str(e)}")
                    continue

            if not records:
                logger.error("No valid records to store after processing")
                return False

            # Construct and execute insert query
            placeholders = ",".join(["?" for _ in fields])
            query = f"""
                INSERT INTO match_data ({",".join(fields)})
                VALUES ({placeholders})
            """

            success = self.execute_query(query, records)
            if success:
                logger.info(f"Successfully stored {len(records)} records")
                return True
            else:
                logger.error("Database operation failed")
                return False

        except Exception as e:
            logger.error(f"Failed to store batch: {str(e)}")
            return False


if __name__ == "__main__":
    scraper = MatchDataScraper(db_path="database/database.db")
    results = scraper.scrape(headless=True, batch_size=10)

    for sport, count in results.items():
        print(f"Scraped {count} matches for {sport}")
