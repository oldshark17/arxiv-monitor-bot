"""Parse user preferences using Gemini LLM to extract arXiv search parameters."""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from google import genai

logger = logging.getLogger(__name__)


# All arXiv categories for the LLM to choose from
ALL_ARXIV_CATEGORIES = {
    # Computer Science
    "cs.AI": "Artificial Intelligence",
    "cs.CL": "Computation and Language (NLP, LLMs)",
    "cs.CV": "Computer Vision and Pattern Recognition",
    "cs.LG": "Machine Learning",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.SD": "Sound (audio processing)",
    "cs.IR": "Information Retrieval",
    "cs.RO": "Robotics",
    "cs.HC": "Human-Computer Interaction",
    
    # Electrical Engineering
    "eess.AS": "Audio and Speech Processing (ASR, TTS)",
    "eess.SP": "Signal Processing",
    "eess.IV": "Image and Video Processing",
    
    # Statistics
    "stat.ML": "Machine Learning (Statistics)",
    
    # Mathematics
    "math.OC": "Optimization and Control",
    
    # Quantitative Biology
    "q-bio.QM": "Quantitative Methods",
}


@dataclass
class ParsedPreferences:
    """Parsed user preferences for arXiv search."""
    
    keywords: list[str]
    categories: list[str]
    explanation: str  # LLM's explanation of the parsing
    
    def to_dict(self) -> dict:
        return {
            "keywords": self.keywords,
            "categories": self.categories,
            "explanation": self.explanation,
        }


class PreferenceParser:
    """Parses natural language preferences into arXiv search parameters using Gemini."""
    
    SYSTEM_PROMPT = """You are an expert at understanding research interests and converting them into arXiv search parameters.

Given a user's description of their research interests, extract:
1. **keywords**: Specific technical terms, model names, or concepts to search for (5-15 keywords)
2. **categories**: Relevant arXiv category codes from the list below

Available arXiv categories:
{categories}

Respond ONLY with valid JSON in this exact format (no markdown, no code blocks):
{{
    "keywords": ["keyword1", "keyword2", ...],
    "categories": ["cs.CL", "cs.LG", ...],
    "explanation": "Brief explanation of why you chose these keywords and categories"
}}

Guidelines:
- Extract specific technical terms (e.g., "transformer", "BERT", "diffusion model")
- Include both acronyms and full forms when relevant (e.g., "ASR", "automatic speech recognition")
- Choose 2-5 most relevant categories
- Be specific with keywords - "large language model" is better than just "AI"
"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash-lite",
    ):
        """
        Initialize the preference parser with Gemini.
        
        Args:
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
            model: Gemini model name to use
        """
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.model_name = model
        
        if not self.api_key:
            logger.warning("No API key provided. Set GOOGLE_API_KEY environment variable.")
        
        self.client = genai.Client(api_key=self.api_key)
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt with available categories."""
        categories_str = "\n".join(
            f"- {code}: {name}" for code, name in ALL_ARXIV_CATEGORIES.items()
        )
        return self.SYSTEM_PROMPT.format(categories=categories_str)
    
    async def parse(self, user_text: str) -> ParsedPreferences:
        """
        Parse user's natural language preferences into search parameters.
        
        Args:
            user_text: User's description of their interests
            
        Returns:
            ParsedPreferences with keywords and categories
        """
        logger.info(f"Parsing preferences: {user_text[:100]}...")
        
        full_prompt = f"{self._build_system_prompt()}\n\nUser's interests: {user_text}"
        
        # Use async generation
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=full_prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0.3,
                response_mime_type="application/json",
            ),
        )
        content = response.text
        
        # Parse JSON response (handle potential markdown wrapping)
        content = content.strip()
        if content.startswith("```"):
            # Remove markdown code block if present
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {content}")
            raise ValueError(f"Invalid JSON from LLM: {e}")
        
        # Validate categories
        valid_categories = [
            cat for cat in parsed.get("categories", [])
            if cat in ALL_ARXIV_CATEGORIES
        ]
        
        result = ParsedPreferences(
            keywords=parsed.get("keywords", []),
            categories=valid_categories or ["cs.CL", "cs.LG"],  # Default fallback
            explanation=parsed.get("explanation", ""),
        )
        
        logger.info(f"Parsed: {len(result.keywords)} keywords, {len(result.categories)} categories")
        return result


async def interactive_preferences():
    """Interactive CLI for testing preference parsing."""
    import asyncio
    from .client import ArxivClient
    
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("arXiv Preference Parser (Gemini)")
    print("=" * 60)
    print("\nDescribe your research interests in natural language.")
    print("Example: 'I'm interested in large language models, especially")
    print("         instruction tuning and RLHF. Also speech synthesis.'")
    print("\n" + "-" * 60)
    
    # Get user input
    user_input = input("\nYour interests: ").strip()
    
    if not user_input:
        print("No input provided. Using default example.")
        user_input = "I'm interested in automatic speech recognition and text-to-speech systems, especially neural approaches like Whisper and VITS."
    
    # Parse preferences
    parser = PreferenceParser()
    
    try:
        prefs = await parser.parse(user_input)
    except Exception as e:
        print(f"\n❌ Error parsing preferences: {e}")
        print("Make sure GOOGLE_API_KEY is set.")
        return
    
    print("\n" + "=" * 60)
    print("Parsed Preferences")
    print("=" * 60)
    print(f"\n📝 Explanation: {prefs.explanation}")
    print(f"\n🔑 Keywords ({len(prefs.keywords)}):")
    for kw in prefs.keywords:
        print(f"   • {kw}")
    print(f"\n📂 Categories ({len(prefs.categories)}):")
    for cat in prefs.categories:
        print(f"   • {cat}: {ALL_ARXIV_CATEGORIES.get(cat, 'Unknown')}")
    
    # Search arXiv with parsed preferences
    print("\n" + "=" * 60)
    print("Searching arXiv...")
    print("=" * 60)
    
    client = ArxivClient()
    articles = await client.search_by_keywords(
        keywords=prefs.keywords[:5],  # Use top 5 keywords
        categories=prefs.categories,
        max_results=5,
    )
    
    if articles:
        print(f"\nFound {len(articles)} articles:\n")
        for i, article in enumerate(articles, 1):
            print(f"{i}. 📄 {article.title}")
            print(f"   👥 {', '.join(article.authors[:2])}{'...' if len(article.authors) > 2 else ''}")
            print(f"   📂 {article.primary_category}")
            print(f"   🔗 {article.abstract_url}")
            print()
    else:
        print("No articles found. Try broader keywords.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(interactive_preferences())
