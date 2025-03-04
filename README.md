# Flashscore Scraper

Build your own local sports database today! A powerful Python package for scraping sports data from Flashscore, enabling data-driven sports analytics, visualization projects, and betting models.

## Overview

Flashscore Scraper is a flexible and efficient tool for collecting sports data from Flashscore.com. Whether you're building predictive models, analyzing team performance, or creating data visualizations, this package provides the foundation for your sports data needs.

Currently supports:
- Football (basic results)
- Handball (match results)
- Volleyball (match results)
- Betting odds data across supported sports

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

## Current Limitations

- Some corner cases where match results are not parseable
- Limited sport coverage (currently football, handball, and volleyball)
- Basic data validation and error handling
- No built-in data analysis tools

## Future Plans

1. **Enhanced Modularity**: Create separate classes for each sport
2. **Data Processing**: Add a data loader for pandas DataFrame conversion
3. **Extended Coverage**: Support for more sports and data types
4. **Improved Validation**: Better handling of edge cases
5. **Analysis Tools**: Built-in functions for common analysis tasks

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
