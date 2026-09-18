from .dedupe import dedupe, normalize_title, normalize_url, title_similarity
from .extract import fetch_bodies

__all__ = [
    "dedupe",
    "normalize_url",
    "normalize_title",
    "title_similarity",
    "fetch_bodies",
]

from .categorize import CATEGORIES, DEFAULT_CATEGORY, assign_category, categorize_documents

__all__ += ["categorize_documents", "assign_category", "CATEGORIES", "DEFAULT_CATEGORY"]
