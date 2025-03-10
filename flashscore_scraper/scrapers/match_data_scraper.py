"""Module for scraping detailed match data from FlashScore."""

import json
import logging
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

    def _calculate_season(self, month: int, year: int) -> str:
        return f"{year}/{year + 1}" if month >= 8 else f"{year - 1}/{year}"

    def _parse_datetime(self, dt_str: str) -> Tuple[str, int, int]:
        date_parts = dt_str.split(".")
        _, month = map(int, date_parts[:2])
        year = int(date_parts[2].split()[0])
        return dt_str, month, year

    def _extract_tournament_info(self, header: Tag) -> Tuple[str, str, str]:
        tournament_info = header.text
        try:
            country = tournament_info.split(":")[0].strip()
            league = tournament_info.split(":")[1].strip().split("-")[0].strip()
            match_info = tournament_info.split(" - ")[-1].strip()
            return country, league, match_info
        except IndexError:
            raise ParsingException("Invalid tournament info format")

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
                    SELECT m.match_id, s.name as sport_name, s.id as sport_id
                    FROM match_ids m
                    JOIN sports s ON m.sport_id = s.id
                    LEFT JOIN match_data d ON m.match_id = d.flashscore_id
                    LEFT JOIN fixtures f ON m.match_id = f.flashscore_id
                    WHERE d.flashscore_id IS NULL AND f.flashscore_id IS NULL
                    ORDER BY s.name, m.created_at
                """)
                matches = cursor.fetchall()

        if not matches:
            return results

        sport_matches = {}
        for match_id, sport_name, sport_id in matches:
            sport_matches.setdefault(sport_name, []).append((match_id, sport_id))

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
                                f"Failed to process match {match_id}: {str(e)}"
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
        match_id: str,
        sport_id: int,
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
                except (ParsingException, ValidationException):
                    try:
                        fixture_data = self._parse_fixture_data(
                            soup, match_id, sport_id
                        )
                        if fixture_data and self._store_fixture(fixture_data):
                            return None
                    except Exception as e:
                        logger.warning(f"Failed to parse as fixture: {str(e)}")

        return None

    def _match_results(
        self, soup: BeautifulSoup, match_id: str, sport_id: int
    ) -> MatchResult:
        header = soup.find("span", class_="tournamentHeader__country")
        if not header or not isinstance(header, Tag):
            raise ParsingException("Could not find tournament header")

        dt_header = header.find_next("div", class_="duelParticipant__startTime")
        if not dt_header:
            raise ParsingException("Could not find start time")

        dt_str, month, year = self._parse_datetime(dt_header.text)
        country, league, match_info = self._extract_tournament_info(header)

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
            home_score, away_score = map(
                int, final_score_element.text.strip().split("-")
            )
            result = (
                1 if home_score > away_score else -1 if home_score < away_score else 0
            )
        except (ValueError, IndexError):
            raise ParsingException("Invalid score format")

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
            for i, part in enumerate(
                match_parts[::2]
            ):  # Skip every other element (scores)
                if i >= len(period_mapping):
                    continue

                try:
                    period_key = list(period_mapping.values())[i]
                    scores = match_parts[i * 2 + 1].text.strip().split("-")
                    details[f"home_score_{period_key}"] = int(scores[0])
                    details[f"away_score_{period_key}"] = int(scores[1])
                except (IndexError, ValueError):
                    continue

            return details if details else None
        except Exception:
            return None

    def _parse_football_details(self, soup: BeautifulSoup) -> Optional[Dict[str, int]]:
        period_mapping = {
            "1st Half": "first_half",
            "2nd Half": "second_half",
            "Extra Time": "extra_time",
            "Penalties": "penalties",
        }
        return self._parse_match_parts(
            soup, period_mapping, ["wcl-overline_rOFfd", "wcl-scores-overline-02_n9EXm"]
        )

    def _parse_handball_details(self, soup: BeautifulSoup) -> Optional[Dict[str, int]]:
        period_mapping = {"First half": "h1", "Second half": "h2"}
        return self._parse_match_parts(
            soup, period_mapping, ["smh__part", "smh__home", "smh__away"]
        )

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
                details[f"home_score_set_{i + 1}"] = int(home_part.get_text(strip=True))
                details[f"away_score_set_{i + 1}"] = int(away_part.get_text(strip=True))
            except (ValueError, AttributeError):
                continue

        return details if details else None

    def _get_match_parts(
        self, soup: BeautifulSoup
    ) -> Optional[Tuple[List[Tag], List[Tag]]]:
        try:
            home_parts = [
                tag
                for tag in soup.find_all(
                    "div",
                    class_=lambda c: "smh__part" in str(c) and "smh__home" in str(c),
                )
                if isinstance(tag, Tag)
            ]
            away_parts = [
                tag
                for tag in soup.find_all(
                    "div",
                    class_=lambda c: "smh__part" in str(c) and "smh__away" in str(c),
                )
                if isinstance(tag, Tag)
            ]
            return (home_parts, away_parts) if home_parts and away_parts else None
        except Exception:
            return None

    def _parse_fixture_data(
        self, soup: BeautifulSoup, match_id: str, sport_id: int
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

            dt_str, month, year = self._parse_datetime(dt_header.text)
            country, league, match_info = self._extract_tournament_info(header)

            return {
                "flashscore_id": match_id,
                "sport_id": sport_id,
                "country": country,
                "league": league,
                "season": self._calculate_season(month, year),
                "match_info": match_info,
                "datetime": dt_str,
                "home_team": home_team.text.strip(),
                "away_team": away_team.text.strip(),
            }
        except Exception:
            return None

    def _store_fixture(self, fixture_data: Dict[str, Any]) -> bool:
        query = """
            INSERT INTO fixtures
            (flashscore_id, sport_id, country, league, season,
             match_info, datetime, home_team, away_team)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flashscore_id) DO UPDATE SET
            datetime = excluded.datetime,
            match_info = excluded.match_info
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

            query = f"INSERT INTO match_data ({','.join(fields)}) VALUES ({','.join(['?' for _ in fields])})"
            return bool(self.execute_query(query, records))

        except Exception as e:
            logger.error(f"Failed to store batch: {str(e)}")
            return False


if __name__ == "__main__":
    scraper = MatchDataScraper(db_path="database/database.db")
    results = scraper.scrape(headless=True, batch_size=10)
    for sport, count in results.items():
        print(f"Scraped {count} matches for {sport}")
