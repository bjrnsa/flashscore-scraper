"""Base models for data validation."""

from datetime import datetime as dt
from typing import Dict, List

from pydantic import BaseModel, ConfigDict, HttpUrl, field_validator


class MatchResult(BaseModel):
    """Pydantic model for match result validation."""

    model_config = ConfigDict(strict=True)

    country: str
    league: str
    season: int
    datetime: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    result: int
    sport_id: int
    flashscore_id: str

    @field_validator("result")
    def validate_result(cls, v: int) -> int:
        """Validate result is within expected range."""
        if v not in [-1, 0, 1]:
            raise ValueError("Result must be -1 (away win), 0 (draw), or 1 (home win)")
        return v

    @field_validator("home_goals", "away_goals")
    def validate_scores(cls, v: int) -> int:
        """Validate scores are non-negative."""
        if v < 0:
            raise ValueError("Scores must be non-negative")
        return v


class LeagueConfig(BaseModel):
    """Pydantic model for league configuration validation."""

    model_config = ConfigDict(strict=True)

    name: str
    country: str
    url: HttpUrl
    seasons: List[int]
    url_pattern: str

    @field_validator("seasons")
    def validate_seasons(cls, seasons: List[int]) -> List[int]:
        """Validate season years are reasonable."""
        current_year = dt.now().year
        for season in seasons:
            if not (2000 <= season <= current_year + 1):
                raise ValueError(
                    f"Season year {season} is not within valid range (2000-{current_year + 1})"
                )
        return sorted(seasons)


class SportConfig(BaseModel):
    """Pydantic model for sport configuration validation."""

    model_config = ConfigDict(strict=True)

    leagues: List[LeagueConfig]


class FlashscoreConfig(BaseModel):
    """Root Pydantic model for configuration validation."""

    model_config = ConfigDict(strict=True)

    sport_ids: Dict[str, SportConfig]


class MatchOdds(BaseModel):
    """Pydantic model for match odds validation."""

    model_config = ConfigDict(strict=True)

    flashscore_id: str
    sport_id: int
    bookmaker: str
    home_odds: float
    draw_odds: float
    away_odds: float

    @field_validator("home_odds", "draw_odds", "away_odds")
    def validate_odds(cls, v: float) -> float:
        """Validate odds are positive numbers."""
        if v < 0:
            raise ValueError("Odds must be non-negative")
        return v
