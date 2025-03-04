# Flashscore Scraper

A Python package for scraping sports data from Flashscore, enabling data-driven sports analytics, visualization projects, and betting models.

## Overview

Flashscore Scraper is a flexible and efficient tool for collecting sports data from Flashscore.com. Whether you're building predictive models, analyzing team performance, or creating data visualizations, this package provides the foundation for your sports data needs.

Currently supports:
- Football (match results)
- Handball (match results)
- Volleyball (match results)
- Historical betting odds data across supported sports

## Features

- **Flexible Data Collection**: Filter matches by sport, league, season, and country
- **Modular Architecture**: Separate scrapers for match IDs, match data, and odds data
- **Efficient Database Management**: SQLite-based storage with optimized queries
- **Rate Limiting**: Built-in protection against rate limiting
- **Batch Processing**: Memory-efficient processing of large datasets
- **Progress Tracking**: Real-time progress monitoring during scraping
- **Error Handling**: Robust error handling and logging

## Installation

```bash
pip install flashscore-scraper
```

## Usage

### Basic Example

```python
from flashscore_scraper import FlexibleScraper

# Initialize scraper with filters
filters = {
    "sports": ["handball"],
    "leagues": ["Kvindeligaen Women"],
    "seasons": ["2023/2024"],
    "countries": ["Denmark"]
}

# Create scraper instance
scraper = FlexibleScraper(filters=filters)

# Start scraping (with optional odds data)
results = scraper.scrape(headless=True, batch_size=100, scrape_odds=True)
```

### Available Filters

You can check available filter values from your database:

```python
scraper = FlexibleScraper()
available_filters = scraper.get_available_filters()
print(available_filters)
```

## Configuration

The package uses a YAML configuration file to specify which leagues and seasons to scrape. Create a `flashscore_urls.yaml` file in your config directory:

```yaml
sports:
  handball:
    leagues:
      - name: "Herre Handbold Ligaen"
        country: "Denmark"
        url: "https://www.flashscore.com/handball/denmark/herre-handbold-ligaen"
        seasons: [2025, 2024]
  volleyball:
    leagues:
      - name: "PlusLiga"
        country: "Poland"
        url: "https://www.flashscore.com/volleyball/poland/plusliga"
        seasons: [2025]
```

### Configuration Structure

- **sports**: Top-level category
  - **sport_name**: (e.g., handball, volleyball)
    - **leagues**: List of league configurations
      - **name**: League name
      - **country**: Country name
      - **url**: Flashscore URL for the league
      - **seasons**: List of years to scrape

## Architecture

The scraper is built with a modular design:

- **FlexibleScraper**: Main entry point with filtering capabilities
- **MatchIDScraper**: Collects match IDs based on configured URLs
- **MatchDataScraper**: Scrapes detailed match information
- **OddsDataScraper**: Collects betting odds data
- **DatabaseManager**: Handles SQLite database operations

## Data Loaders

The package includes specialized data loaders for each supported sport, providing easy access to the collected data in pandas DataFrame format:

### Base Features (All Sports)

- Load match data with flexible filtering (league, season, date range)
- Process JSON-based additional data fields
- Load and merge odds data
- Validate data consistency
- Common features like team indices, goal differences, and total scores
- Efficient SQLite database queries

### Sport-Specific Loaders

#### Football (`Football` class)
- Goals and half-time scores
- First and second half scoring
- Example:
```python
from flashscore_scraper.data_loaders import Football

loader = Football()
df = loader.load_matches(
    league="Premier League",
    seasons=["2023/2024"],
    include_additional_data=True
)
```

#### Handball (`Handball` class)
- Goals and half-time scores
- First and second half scoring
- Example:
```python
from flashscore_scraper.data_loaders import Handball

loader = Handball()
df = loader.load_matches(
    league="Herre Handbold Ligaen",
    include_additional_data=True
)
```

#### Volleyball (`Volleyball` class)
- Set scores and points per set (up to 5 sets)
- Total points calculation
- Set-specific validation
- Example:
```python
from flashscore_scraper.data_loaders import Volleyball

loader = Volleyball()
df = loader.load_matches(
    league="PlusLiga",
    include_additional_data=True
)
```

## Current Limitations

- Some corner cases where match results are not parseable
- Limited sport coverage (currently football, handball, and volleyball)
- Basic odds data validation
- Limited built-in analysis tools

## Future Plans

1. **Enhanced Modularity**: Create separate classes for each sport
2. **Extended Coverage**: Support for more sports and data types
3. **Improved Validation**: Better handling of edge cases
4. **Analysis Tools**: Built-in functions for common analysis tasks
5. **Advanced Features**: Support for player statistics and advanced metrics

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
