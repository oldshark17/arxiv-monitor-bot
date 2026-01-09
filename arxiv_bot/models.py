"""Data models for arXiv articles."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ArxivArticle:
    """Represents an arXiv article with its metadata."""
    
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    published_date: datetime
    updated_date: datetime
    pdf_url: str
    abstract_url: str
    primary_category: str = ""
    comment: Optional[str] = None
    journal_ref: Optional[str] = None
    doi: Optional[str] = None
    
    def __post_init__(self):
        """Set primary category if not provided."""
        if not self.primary_category and self.categories:
            self.primary_category = self.categories[0]
    
    @property
    def short_id(self) -> str:
        """Get the short arXiv ID without version."""
        return self.arxiv_id.split("v")[0]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "categories": self.categories,
            "published_date": self.published_date.isoformat(),
            "updated_date": self.updated_date.isoformat(),
            "pdf_url": self.pdf_url,
            "abstract_url": self.abstract_url,
            "primary_category": self.primary_category,
            "comment": self.comment,
            "journal_ref": self.journal_ref,
            "doi": self.doi,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ArxivArticle":
        """Create an ArxivArticle from a dictionary."""
        return cls(
            arxiv_id=data["arxiv_id"],
            title=data["title"],
            abstract=data["abstract"],
            authors=data["authors"],
            categories=data["categories"],
            published_date=datetime.fromisoformat(data["published_date"]),
            updated_date=datetime.fromisoformat(data["updated_date"]),
            pdf_url=data["pdf_url"],
            abstract_url=data["abstract_url"],
            primary_category=data.get("primary_category", ""),
            comment=data.get("comment"),
            journal_ref=data.get("journal_ref"),
            doi=data.get("doi"),
        )
    
    def format_summary(self) -> str:
        """Format article for display (e.g., in Telegram)."""
        authors_str = ", ".join(self.authors[:3])
        if len(self.authors) > 3:
            authors_str += f" et al. ({len(self.authors)} authors)"
        
        return (
            f"📄 **{self.title}**\n\n"
            f"👥 {authors_str}\n"
            f"📂 {self.primary_category}\n"
            f"📅 {self.published_date.strftime('%Y-%m-%d')}\n\n"
            f"📝 {self.abstract[:300]}...\n\n"
            f"🔗 [PDF]({self.pdf_url}) | [Abstract]({self.abstract_url})"
        )
