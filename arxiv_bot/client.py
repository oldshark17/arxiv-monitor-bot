"""arXiv API client for fetching and searching papers."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import arxiv

from .models import ArxivArticle

logger = logging.getLogger(__name__)


class ArxivClient:
    """Client for interacting with the arXiv API."""
    
    def __init__(
        self,
        max_results_per_query: int = 50,
        rate_limit_delay: float = 3.0,  # arXiv asks for 3 seconds between requests
    ):
        """
        Initialize the arXiv client.
        
        Args:
            max_results_per_query: Maximum number of results per API call
            rate_limit_delay: Delay between API requests in seconds
        """
        self.max_results_per_query = max_results_per_query
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time: Optional[datetime] = None
    
    async def _rate_limit(self) -> None:
        """Enforce rate limiting between API requests."""
        if self._last_request_time is not None:
            elapsed = (datetime.now() - self._last_request_time).total_seconds()
            if elapsed < self.rate_limit_delay:
                await asyncio.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = datetime.now()
    
    def _convert_result(self, result: arxiv.Result) -> ArxivArticle:
        """Convert an arxiv.Result to our ArxivArticle model."""
        return ArxivArticle(
            arxiv_id=result.entry_id.split("/abs/")[-1],
            title=result.title.replace("\n", " ").strip(),
            abstract=result.summary.replace("\n", " ").strip(),
            authors=[author.name for author in result.authors],
            categories=result.categories,
            published_date=result.published,
            updated_date=result.updated,
            pdf_url=result.pdf_url,
            abstract_url=result.entry_id,
            primary_category=result.primary_category,
            comment=result.comment,
            journal_ref=result.journal_ref,
            doi=result.doi,
        )
    
    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        sort_by: arxiv.SortCriterion = arxiv.SortCriterion.SubmittedDate,
        sort_order: arxiv.SortOrder = arxiv.SortOrder.Descending,
    ) -> list[ArxivArticle]:
        """
        Search arXiv for papers matching the query.
        
        Args:
            query: arXiv search query string
            max_results: Maximum number of results (defaults to max_results_per_query)
            sort_by: Sort criterion (default: submission date)
            sort_order: Sort order (default: descending/newest first)
            
        Returns:
            List of ArxivArticle objects
        """
        await self._rate_limit()
        
        max_results = max_results or self.max_results_per_query
        
        logger.info(f"Searching arXiv with query: {query[:100]}...")
        
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        
        # Run the synchronous arxiv library in a thread pool
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: list(search.results())
        )
        
        articles = [self._convert_result(r) for r in results]
        logger.info(f"Found {len(articles)} articles")
        
        return articles
    
    async def search_by_category(
        self,
        categories: list[str],
        max_results: Optional[int] = None,
        days_back: int = 7,
    ) -> list[ArxivArticle]:
        """
        Search for recent papers in specific arXiv categories.
        
        Args:
            categories: List of arXiv category codes (e.g., ['cs.CL', 'cs.SD'])
            max_results: Maximum number of results
            days_back: How many days back to search
            
        Returns:
            List of ArxivArticle objects
        """
        # Build category query
        cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
        query = f"({cat_query})"
        
        return await self.search(query, max_results=max_results)
    
    async def search_by_keywords(
        self,
        keywords: list[str],
        categories: Optional[list[str]] = None,
        search_in: str = "all",  # "all", "title", "abstract"
        max_results: Optional[int] = None,
    ) -> list[ArxivArticle]:
        """
        Search for papers matching keywords.
        
        Args:
            keywords: List of keywords to search for
            categories: Optional list of categories to filter by
            search_in: Where to search ("all", "title", "abstract")
            max_results: Maximum number of results
            
        Returns:
            List of ArxivArticle objects
        """
        # Build keyword query based on search location
        field_prefix = {
            "title": "ti:",
            "abstract": "abs:",
            "all": "all:",
        }.get(search_in, "all:")
        
        keyword_parts = []
        for kw in keywords:
            # Quote multi-word keywords
            if " " in kw:
                keyword_parts.append(f'{field_prefix}"{kw}"')
            else:
                keyword_parts.append(f"{field_prefix}{kw}")
        
        query = " OR ".join(keyword_parts)
        
        # Add category filter if specified
        if categories:
            cat_query = " OR ".join([f"cat:{cat}" for cat in categories])
            query = f"({query}) AND ({cat_query})"
        
        return await self.search(query, max_results=max_results)
    
    async def get_article_by_id(self, arxiv_id: str) -> Optional[ArxivArticle]:
        """
        Get a specific article by its arXiv ID.
        
        Args:
            arxiv_id: The arXiv ID (e.g., "2301.07041" or "2301.07041v1")
            
        Returns:
            ArxivArticle if found, None otherwise
        """
        await self._rate_limit()
        
        logger.info(f"Fetching article: {arxiv_id}")
        
        search = arxiv.Search(id_list=[arxiv_id])
        
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: list(search.results())
        )
        
        if results:
            return self._convert_result(results[0])
        return None
    
    async def get_recent_papers(
        self,
        categories: list[str],
        keywords: Optional[list[str]] = None,
        max_results: int = 20,
    ) -> list[ArxivArticle]:
        """
        Get recent papers matching criteria (categories and/or keywords).
        
        Args:
            categories: List of arXiv categories to search (required)
            keywords: List of keywords to search for
            max_results: Maximum number of results
            
        Returns:
            List of ArxivArticle objects sorted by date
        """
        if keywords:
            return await self.search_by_keywords(
                keywords=keywords,
                categories=categories,
                max_results=max_results,
            )
        else:
            return await self.search_by_category(
                categories=categories,
                max_results=max_results,
            )


async def main():
    """Test the arXiv client."""
    logging.basicConfig(level=logging.INFO)
    
    client = ArxivClient()
    
    # Test 1: Search by category
    print("\n" + "="*60)
    print("Test 1: Recent papers in cs.CL (Computation and Language)")
    print("="*60)
    articles = await client.search_by_category(["cs.CL"], max_results=5)
    for article in articles:
        print(f"\n📄 {article.title}")
        print(f"   Authors: {', '.join(article.authors[:2])}...")
        print(f"   Date: {article.published_date.strftime('%Y-%m-%d')}")
        print(f"   URL: {article.abstract_url}")
    
    # Test 2: Search by keywords
    print("\n" + "="*60)
    print("Test 2: Search for 'large language model' papers")
    print("="*60)
    articles = await client.search_by_keywords(
        ["large language model"],
        categories=["cs.CL", "cs.AI"],
        max_results=5,
    )
    for article in articles:
        print(f"\n📄 {article.title}")
        print(f"   Category: {article.primary_category}")
        print(f"   Date: {article.published_date.strftime('%Y-%m-%d')}")
    
    # Test 3: Get specific article
    print("\n" + "="*60)
    print("Test 3: Get specific article by ID")
    print("="*60)
    # This is the "Attention Is All You Need" paper
    article = await client.get_article_by_id("1706.03762")
    if article:
        print(f"\n📄 {article.title}")
        print(f"   Authors: {', '.join(article.authors)}")
        print(f"   Abstract: {article.abstract[:200]}...")


if __name__ == "__main__":
    asyncio.run(main())
