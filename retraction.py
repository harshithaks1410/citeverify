"""Retraction detection module for CiteVerify.

Integrates Crossref updates API and PubMed publication type tags to detect
retracted papers, identify retraction notices, and exclude them from consensus.
"""

import logging
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "CiteVerify/1.0 (Open-Source Scientific Fact-Checker; mailto:contact@citeverify.org)"
CROSSREF_UPDATES_URL = "https://api.crossref.org/works"
REQUEST_TIMEOUT = 8  # seconds

# In-memory cache for retraction lookups: doi -> RetractionStatus
_RETRACTION_CACHE: Dict[str, "RetractionStatus"] = {}


@dataclass
class RetractionStatus:
    is_retracted: bool
    reason: Optional[str] = None
    notice_doi: Optional[str] = None
    notice_title: Optional[str] = None
    source: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def check_pubmed_retraction(publication_types: List[str]) -> Optional[RetractionStatus]:
    """Check PubMed publication type tags for retraction flags."""
    retraction_keywords = [
        "retracted publication",
        "retraction of publication",
    ]
    for pt in publication_types:
        if pt.strip().lower() in retraction_keywords:
            return RetractionStatus(
                is_retracted=True,
                reason="Flagged as 'Retracted Publication' in PubMed index.",
                source="PubMed",
            )
    return None


def check_crossref_retraction(doi: str) -> RetractionStatus:
    """Query Crossref API for update notices (retractions/withdrawals) matching the DOI."""
    clean_doi = doi.strip().lower()
    if clean_doi in _RETRACTION_CACHE:
        return _RETRACTION_CACHE[clean_doi]

    headers = {"User-Agent": USER_AGENT}
    params = {
        "filter": f"updates:{clean_doi}",
        "rows": 5,
    }

    try:
        res = requests.get(
            CROSSREF_UPDATES_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if res.status_code == 200:
            data = res.json()
            items = data.get("message", {}).get("items", [])
            for item in items:
                # Check update-to metadata
                update_list = item.get("update-to", [])
                for update in update_list:
                    update_type = (update.get("type") or "").lower()
                    label = (update.get("label") or "").lower()
                    if "retraction" in update_type or "retraction" in label or "withdrawn" in update_type:
                        title_list = item.get("title", [])
                        notice_title = title_list[0] if title_list else "Retraction Notice"
                        notice_doi = item.get("DOI")
                        status = RetractionStatus(
                            is_retracted=True,
                            reason=f"Crossref retraction notice: {notice_title}",
                            notice_doi=notice_doi,
                            notice_title=notice_title,
                            source="Crossref",
                        )
                        _RETRACTION_CACHE[clean_doi] = status
                        return status

                # Also inspect titles of update items
                for title in item.get("title", []):
                    if re.search(r"\b(retraction|withdrawn|retracted)\b", title, re.IGNORECASE):
                        notice_doi = item.get("DOI")
                        status = RetractionStatus(
                            is_retracted=True,
                            reason=f"Crossref update item indicates retraction: {title}",
                            notice_doi=notice_doi,
                            notice_title=title,
                            source="Crossref",
                        )
                        _RETRACTION_CACHE[clean_doi] = status
                        return status

    except Exception as e:
        logger.warning("Crossref retraction check failed for DOI %s: %s", doi, e)

    status = RetractionStatus(is_retracted=False)
    _RETRACTION_CACHE[clean_doi] = status
    return status


def verify_paper_retraction(doi: Optional[str], publication_types: List[str]) -> RetractionStatus:
    """Combine PubMed tags and Crossref checks to determine retraction status."""
    # Step 1: Immediate check via PubMed publication types
    pubmed_status = check_pubmed_retraction(publication_types)
    if pubmed_status:
        return pubmed_status

    # Step 2: Crossref check if DOI is available
    if doi:
        return check_crossref_retraction(doi)

    return RetractionStatus(is_retracted=False)
