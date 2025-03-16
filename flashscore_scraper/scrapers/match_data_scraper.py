"""Module for scraping detailed match data from FlashScore."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, Tag
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm

from flashscore_scraper.core.browser import BrowserManager
from flashscore_scraper.exceptions import ParsingException, ValidationException
from flashscore_scraper.models.base import MatchResult
from flashscore_scraper.scrapers.base import BaseScraper

logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class MatchDataScraper(BaseScraper):
    """Scrapes detailed match data and stores it in a structured format."""

    BASE_URL = "https://www.flashscore.com/match/"
    DEFAULT_BATCH_SIZE = 100
    TIMEOUT = 5
    CLICK_DELAY = 0.5
    MAX_REQUESTS_PER_MINUTE = 120
    ONE_MINUTE = 60
    MAX_RETRIES = 20

    def __init__(self, db_path: str = "database/database.db"):
        """Initialize the MatchDataScraper."""
        super().__init__(db_path)
        self.db_manager = self.get_database()

    def update(
        self,
        days: int = 3,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
    ) -> Dict[str, int]:
        """Update match data for matches from the last specified days.

        Parameters
        ----------
        days : int, optional
            Number of days to look back, by default 3
        batch_size : int, optional
            Size of batches for processing, by default DEFAULT_BATCH_SIZE
        headless : bool, optional
            Whether to run browser in headless mode, by default True

        Returns:
        -------
        Dict[str, int]
            Dictionary with sport names as keys and number of updated matches as values
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT m.flashscore_id, s.name as sport_name, s.id as sport_id,
                    m.country, m.league, m.season
                FROM match_data m
                JOIN sport_ids s ON m.sport_id = s.id
                WHERE DATE(m.datetime) >= DATE('now', '-' || ? || ' days')
                ORDER BY s.name, m.datetime DESC
            """,
                (days,),
            )
            matches = cursor.fetchall()

        return self.scrape(batch_size=batch_size, headless=headless, matches=matches)

    def update_upcoming_fixtures(
        self,
        days: int = 3,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
    ) -> Dict[str, int]:
        """Update upcoming fixtures for matches from the last specified days.

        Parameters
        ----------
        days : int, optional
            Number of days to look back, by default 3
        batch_size : int, optional
            Size of batches for processing, by default DEFAULT_BATCH_SIZE
        headless : bool, optional
            Whether to run browser in headless mode, by default True

        Returns:
        -------
        Dict[str, int]
            Dictionary with sport names as keys and number of updated matches as values
        """
        with self.db_manager.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT m.flashscore_id, s.name as sport_name, s.id as sport_id,
                    m.country, m.league, m.season
                FROM upcoming_fixtures m
                JOIN sport_ids s ON m.sport_id = s.id
                WHERE DATE(m.datetime) <= DATE('now', '-' || ? || ' days')
                ORDER BY s.name, m.datetime DESC
            """,
                (days,),
            )
            matches = cursor.fetchall()

        return self.scrape(batch_size=batch_size, headless=headless, matches=matches)

    def scrape(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        headless: bool = True,
        matches: List[Any] = [],
    ) -> Dict[str, int]:
        """Scrape detailed match data from FlashScore."""
        results = {}

        if not matches:
            with self.db_manager.get_cursor() as cursor:
                cursor.execute("""
                    SELECT m.flashscore_id, s.name as sport_name, s.id as sport_id, m.country, m.league, m.season
                    FROM flashscore_ids m
                    JOIN sport_ids s ON m.sport_id = s.id
                    LEFT JOIN match_data d ON m.flashscore_id = d.flashscore_id
                    LEFT JOIN upcoming_fixtures f ON m.flashscore_id = f.flashscore_id
                    WHERE d.flashscore_id IS NULL AND f.flashscore_id IS NULL
                    ORDER BY s.name, m.created_at
                """)
                matches = cursor.fetchall()

        if not matches:
            return results

        sport_matches = {}
        for flashscore_id, sport_name, sport_id, country, league, season in matches:
            sport_matches.setdefault(sport_name, []).append(
                (flashscore_id, sport_id, country, league, season)
            )

        browser = self.get_browser(headless)
        try:
            for sport_name, sport_data in sport_matches.items():
                data_buffer = []
                success_count = 0

                with tqdm(
                    total=len(sport_data), desc=f"Scraping {sport_name} matches"
                ) as pbar:
                    for flashscore_id, sport_id, country, league, season in sport_data:
                        try:
                            url = f"{self.BASE_URL}{flashscore_id}/#/match"
                            match_data = self._process_match(
                                browser,
                                url,
                                flashscore_id,
                                sport_id,
                                country,
                                league,
                                season,
                                sport_name,
                            )

                            if match_data:
                                data_buffer.append(match_data)
                                success_count += 1

                                if len(
                                    data_buffer
                                ) >= batch_size and not self._store_batch(data_buffer):
                                    logger.error("Failed to store batch")
                                data_buffer = (
                                    []
                                    if len(data_buffer) >= batch_size
                                    else data_buffer
                                )

                        except Exception as e:
                            logger.error(
                                f"Failed to process match {flashscore_id}: {str(e)}"
                            )
                        finally:
                            pbar.update(1)

                    if data_buffer and not self._store_batch(data_buffer):
                        logger.error("Failed to store final batch")

                results[sport_name] = success_count
        finally:
            browser.close()

        return results

    def _process_match(
        self,
        browser: BrowserManager,
        url: str,
        flashscore_id: str,
        sport_id: int,
        country: str,
        league: str,
        season: int,
        sport_name: str,
    ) -> Optional[Dict[str, Any]]:
        with browser.get_driver(url) as driver:
            for _ in range(self.MAX_RETRIES):
                soup = BeautifulSoup(driver.page_source, "html.parser")
                WebDriverWait(driver, self.TIMEOUT).until(
                    EC.presence_of_element_located(
                        (By.CLASS_NAME, "tournamentHeader__country")
                    )
                )

                try:
                    details = self._match_results(
                        soup, flashscore_id, sport_id, country, league, season
                    ).model_dump()
                    additional = self._parse_additional_details(soup, sport_name)

                    if details and additional:
                        details.update(
                            {
                                "flashscore_id": flashscore_id,
                                "sport_id": sport_id,
                                "additional": additional,
                            }
                        )
                        return details
                except (ParsingException, ValidationException):
                    try:
                        fixture_data = self._parse_fixture_data(
                            soup, flashscore_id, sport_id, country, league, season
                        )
                        if fixture_data and self._store_fixture(fixture_data):
                            return None
                    except Exception as e:
                        logger.warning(f"Failed to parse as fixture: {str(e)}")

        return None

    def _match_results(
        self,
        soup: BeautifulSoup,
        flashscore_id: str,
        sport_id: int,
        country: str,
        league: str,
        season: int,
    ) -> MatchResult:
        header = soup.find("span", class_="tournamentHeader__country")
        if not header or not isinstance(header, Tag):
            raise ParsingException("Could not find tournament header")

        dt_header = header.find_next("div", class_="duelParticipant__startTime")
        if not dt_header:
            raise ParsingException("Could not find start time")

        dt_str = datetime.strptime(dt_header.text, "%d.%m.%Y %H:%M").isoformat()
        home_team = soup.select_one(
            ".duelParticipant__home .participant__participantName"
        )
        away_team = soup.select_one(
            ".duelParticipant__away .participant__participantName"
        )
        if not home_team or not away_team:
            raise ParsingException("Could not find team names")

        final_score_element = soup.select_one(".detailScore__wrapper")
        if not final_score_element:
            raise ParsingException("Could not find final score")

        try:
            home_goals, away_goals = map(
                int, final_score_element.text.strip().split("-")
            )
            result = (
                1 if home_goals > away_goals else -1 if home_goals < away_goals else 0
            )
        except (ValueError, IndexError):
            raise ParsingException("Invalid score format")

        return MatchResult(
            country=country,
            league=league,
            season=season,
            datetime=dt_str,
            home_team=home_team.text.strip(),
            away_team=away_team.text.strip(),
            home_goals=home_goals,
            away_goals=away_goals,
            result=result,
            sport_id=sport_id,
            flashscore_id=flashscore_id,
        )

    def _parse_additional_details(
        self, soup: BeautifulSoup, sport: str
    ) -> Optional[Dict[str, int]]:
        parsers = {
            "handball": self._parse_handball_details,
            "volleyball": self._parse_volleyball_details,
            "football": self._parse_football_details,
        }

        try:
            parser = parsers.get(sport.lower())
            return parser(soup) if parser else None
        except Exception as e:
            logger.error(f"Error parsing additional details for {sport}: {str(e)}")
            return None

    def _parse_match_parts(
        self,
        soup: BeautifulSoup,
        period_mapping: Dict[str, str],
        class_filter: List[str],
    ) -> Optional[Dict[str, int]]:
        try:
            match_parts = soup.find_all(
                class_=lambda c: any(f in str(c) for f in class_filter)
            )
            if not match_parts:
                return None

            details = {}
            for i, part in enumerate(match_parts[::2]):
                if i >= len(period_mapping):
                    continue

                try:
                    period_key = list(period_mapping.values())[i]
                    scores = match_parts[i * 2 + 1].text.strip().split("-")
                    details[f"home_goals_{period_key}"] = int(scores[0])
                    details[f"away_goals_{period_key}"] = int(scores[1])
                except (IndexError, ValueError):
                    continue

            return details if details else None
        except Exception:
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
                        home_goals, away_goals = map(int, score_text.split("-"))

                        key = period_mapping[period_name]
                        details[f"home_goals_{key}"] = home_goals
                        details[f"away_goals_{key}"] = away_goals
                    except (IndexError, ValueError, AttributeError):
                        logger.warning(f"Failed to parse score for {period_name}")
                        continue

            return details if details else None

        except Exception as e:
            logger.error(f"Error parsing football details: {str(e)}")
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
                    details[f"home_goals_{period_key}"] = home_value
                    details[f"away_goals_{period_key}"] = away_value
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
        parts = self._get_match_parts(soup)
        if not parts:
            return None

        home_parts, away_parts = parts
        details = {}

        for i, (home_part, away_part) in enumerate(zip(home_parts, away_parts)):
            try:
                details[f"home_goals_set_{i + 1}"] = int(home_part.get_text(strip=True))
                details[f"away_goals_set_{i + 1}"] = int(away_part.get_text(strip=True))
            except (ValueError, AttributeError):
                continue

        return details if details else None

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

    def _parse_fixture_data(
        self,
        soup: BeautifulSoup,
        flashscore_id: str,
        sport_id: int,
        country: str,
        league: str,
        season: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            header = soup.find("span", class_="tournamentHeader__country")
            dt_header = soup.find("div", class_="duelParticipant__startTime")
            home_team = soup.select_one(
                ".duelParticipant__home .participant__participantName"
            )
            away_team = soup.select_one(
                ".duelParticipant__away .participant__participantName"
            )

            if not all([header, dt_header, home_team, away_team]):
                return None

            dt_str = datetime.strptime(dt_header.text, "%d.%m.%Y %H:%M")

            return {
                "flashscore_id": flashscore_id,
                "sport_id": sport_id,
                "country": country,
                "league": league,
                "season": season,
                "datetime": dt_str,
                "home_team": home_team.text.strip(),
                "away_team": away_team.text.strip(),
            }
        except Exception:
            return None

    def _store_fixture(self, fixture_data: Dict[str, Any]) -> bool:
        query = """
            INSERT INTO upcoming_fixtures
            (flashscore_id, sport_id, country, league, season,
                 datetime, home_team, away_team)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flashscore_id) DO UPDATE SET
            datetime = excluded.datetime
        """
        try:
            with self.db_manager.get_cursor() as cursor:
                cursor.execute(query, tuple(fixture_data.values()))
            return True
        except Exception as e:
            logger.error(f"Failed to store fixture: {str(e)}")
            return False

    def _store_batch(self, data: List[Dict[str, Any]]) -> bool:
        fields = [
            "country",
            "league",
            "season",
            "datetime",
            "home_team",
            "away_team",
            "home_goals",
            "away_goals",
            "result",
            "sport_id",
            "flashscore_id",
            "additional_data",
        ]

        try:
            if not data:
                return False

            records = []
            for match in data:
                try:
                    if "additional" in match:
                        match["additional_data"] = json.dumps(match.pop("additional"))
                    records.append(tuple(match.get(field) for field in fields))
                except Exception:
                    continue

            if not records:
                return False

            # Changed to REPLACE to handle updates of existing records
            query = f"""
                INSERT OR REPLACE INTO match_data
                ({",".join(fields)})
                VALUES ({",".join(["?" for _ in fields])})
            """
            return bool(self.execute_query(query, records))

        except Exception as e:
            logger.error(f"Failed to store batch: {str(e)}")
            return False


if __name__ == "__main__":
    scraper = MatchDataScraper(db_path="database/database.db")
    # results = scraper.update(days=10, batch_size=100, headless=True)
    results = scraper.update_upcoming_fixtures(days=0, batch_size=100, headless=True)
