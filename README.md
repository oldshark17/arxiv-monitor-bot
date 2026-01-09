# arXiv Monitor Bot

A Telegram bot that monitors arXiv papers based on user interests and provides LLM-generated summaries.

## Features

- **Natural Language Preferences**: Describe your research interests in plain text, and an LLM extracts relevant keywords and arXiv categories
- **arXiv Integration**: Search and monitor papers by category, keywords, or specific article ID
- **Smart Parsing**: Uses Gemini 2.5 Flash Lite to convert user preferences into structured search parameters

## Setup

1. **Install dependencies**:
   ```bash
   uv venv
   source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
   uv pip install -r requirements.txt
   ```

2. **Set up API key**:
   ```bash
   export GOOGLE_API_KEY="your-gemini-api-key"
   ```

3. **Test the arXiv client**:
   ```bash
   python -m arxiv_bot.client
   ```

4. **Test preference parsing**:
   ```bash
   python -m arxiv_bot.preference_parser
   ```

## Project Structure

```
arxiv_bot/
├── arxiv_bot/
│   ├── __init__.py
│   ├── models.py              # ArxivArticle dataclass
│   ├── client.py              # arXiv API client
│   └── preference_parser.py   # LLM-based preference parsing
├── requirements.txt
└── README.md
```

## Usage Example

```python
from arxiv_bot.preference_parser import PreferenceParser
from arxiv_bot.client import ArxivClient

# Parse user preferences
parser = PreferenceParser()
prefs = await parser.parse("I'm interested in ASR and TTS research")

# Search arXiv
client = ArxivClient()
articles = await client.search_by_keywords(
    keywords=prefs.keywords,
    categories=prefs.categories,
    max_results=10
)
```

## TODO

- [ ] SQLite database for storing user preferences and seen articles
- [ ] Scheduled monitoring service
- [ ] Telegram bot integration
- [ ] LLM-generated paper summaries
- [ ] Article search functionality

## License

MIT
