"""End-to-end verification pipeline, in-memory caching, and streaming engine.

Coordinates query generation, paper retrieval, retraction checks, stance
verification, consensus calculation, and plain-language summary generation.
"""

import hashlib
import json
import logging
import re
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Dict, Generator, List, Optional

import requests

from retraction import verify_paper_retraction
from search import Paper, search_papers
from verify import (
    breakdown_compound_claim,
    calculate_paper_quality_weight,
    compute_consensus,
    extract_claims_from_text,
    extract_study_metadata_regex,
    generate_plain_language_summary,
    generate_search_query,
    judge_paper_stance,
)

logger = logging.getLogger(__name__)

USER_AGENT = "CiteVerify/1.0 (Open-Source Scientific Fact-Checker; mailto:contact@citeverify.org)"
CACHE_TTL_SECONDS = 3600  # 1 hour in-memory cache

# In-memory cache: hash(key) -> (timestamp, data)
_PIPELINE_CACHE: Dict[str, tuple] = {}


class SimpleHTMLTextExtractor(HTMLParser):
    """HTML parser to extract clean article text while stripping markup, scripts, and navigation."""

    def __init__(self):
        super().__init__()
        self.in_ignored_tag = False
        self.ignored_tags = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}
        self.text_chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.ignored_tags:
            self.in_ignored_tag = True

    def handle_endtag(self, tag):
        if tag.lower() in self.ignored_tags:
            self.in_ignored_tag = False

    def handle_data(self, data):
        if not self.in_ignored_tag:
            clean = data.strip()
            if clean:
                self.text_chunks.append(clean)

    def get_text(self) -> str:
        return " ".join(self.text_chunks)


def extract_text_from_url(url: str) -> str:
    """Fetch URL and extract clean body text."""
    headers = {"User-Agent": USER_AGENT}
    res = requests.get(url, headers=headers, timeout=12)
    res.raise_for_status()

    parser = SimpleHTMLTextExtractor()
    parser.feed(res.text)
    return parser.get_text()


def _get_cache_key(prefix: str, text: str) -> str:
    """Generate SHA256 cache key."""
    h = hashlib.sha256(f"{prefix}:{text.strip().lower()}".encode("utf-8")).hexdigest()
    return h


def get_cached_result(prefix: str, text: str) -> Optional[Dict[str, Any]]:
    """Retrieve unexpired cached result."""
    key = _get_cache_key(prefix, text)
    if key in _PIPELINE_CACHE:
        ts, data = _PIPELINE_CACHE[key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return data
        else:
            del _PIPELINE_CACHE[key]
    return None


def set_cached_result(prefix: str, text: str, data: Dict[str, Any]):
    """Store result in in-memory cache with current timestamp."""
    key = _get_cache_key(prefix, text)
    _PIPELINE_CACHE[key] = (time.time(), data)


def run_verification_pipeline(claim: str) -> Dict[str, Any]:
    """Execute synchronous verification for a single claim."""
    claim_clean = claim.strip()
    cached = get_cached_result("claim", claim_clean)
    if cached:
        return cached

    # 1. Search query generation
    query = generate_search_query(claim_clean)
    if not query:
        query = claim_clean

    # 2. Multi-source paper search
    papers = search_papers(query, max_per_source=6)

    # 3. Check for compound claim
    sub_claims = breakdown_compound_claim(claim_clean)
    is_compound = len(sub_claims) > 1

    # 4. Evaluate each paper
    evaluated_papers = []
    for paper in papers:
        # Retraction check
        retraction_status = verify_paper_retraction(paper.doi, paper.publication_types)

        # Study design and sample size extraction
        study_type, sample_size = extract_study_metadata_regex(
            paper.title, paper.abstract, paper.publication_types
        )

        # Quality weight calculation
        quality_weight = calculate_paper_quality_weight(
            study_type, paper.citation_count, paper.year
        )

        # Stance judging & anti-fabrication quote verification
        stance_info = judge_paper_stance(claim_clean, paper)

        paper_dict = paper.to_dict()
        paper_dict.update({
            "retraction": retraction_status.to_dict(),
            "study_type": study_type,
            "sample_size": sample_size,
            "quality_weight": quality_weight,
            "stance": stance_info["stance"],
            "key_sentence": stance_info["key_sentence"],
            "quote_verified": stance_info["quote_verified"],
            "stance_confidence": stance_info["confidence"],
            "reasoning": stance_info["reasoning"],
        })
        evaluated_papers.append(paper_dict)

    # 5. Compute consensus and verdict
    consensus = compute_consensus(claim_clean, evaluated_papers)

    # 6. Generate plain-language summary
    summary = generate_plain_language_summary(consensus)
    consensus["summary"] = summary
    consensus["search_query"] = query
    consensus["is_compound"] = is_compound
    consensus["sub_claims"] = sub_claims
    consensus["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    set_cached_result("claim", claim_clean, consensus)
    return consensus


def stream_verification_pipeline(claim: str) -> Generator[str, None, None]:
    """Yield Server-Sent Events (SSE) updates as the verification pipeline progresses."""
    claim_clean = claim.strip()

    def sse_message(stage: str, message: str, pct: int, **kwargs) -> str:
        payload = {"stage": stage, "message": message, "progress": pct, **kwargs}
        return f"data: {json.dumps(payload)}\n\n"

    yield sse_message("init", "Initializing verification pipeline...", 5)

    # Check cache
    cached = get_cached_result("claim", claim_clean)
    if cached:
        yield sse_message("cached", "Retrieved result from in-memory cache.", 90)
        yield sse_message("complete", "Verification complete.", 100, result=cached)
        return

    # 1. Query generation
    yield sse_message("query", "Generating targeted academic search keywords with Gemini...", 15)
    query = generate_search_query(claim_clean)
    if not query:
        query = claim_clean
    yield sse_message("query_done", f"Target keywords: '{query}'", 25, query=query)

    # 2. Search
    yield sse_message("search", "Searching PubMed and Semantic Scholar in parallel...", 35)
    papers = search_papers(query, max_per_source=6)
    yield sse_message(
        "search_done",
        f"Retrieved and deduplicated {len(papers)} peer-reviewed papers with abstracts.",
        50,
        papers_found=len(papers),
    )

    # 3. Compound claim check
    sub_claims = breakdown_compound_claim(claim_clean)
    is_compound = len(sub_claims) > 1

    # 4. Evaluate papers
    evaluated_papers = []
    total_p = len(papers)

    if total_p == 0:
        yield sse_message("evaluating", "No peer-reviewed papers found for this query.", 80)
    else:
        yield sse_message("evaluating", f"Cross-checking retractions and verifying quotes in {total_p} papers...", 55)

    for i, paper in enumerate(papers):
        # Progress slice: 55% to 85%
        cur_pct = 55 + int(30 * ((i + 1) / max(1, total_p)))
        yield sse_message(
            "paper_eval",
            f"Analyzing study quality & evidence for paper {i + 1}/{total_p}...",
            cur_pct,
            current=i + 1,
            total=total_p,
            paper_title=paper.title[:50] + "...",
        )

        retraction_status = verify_paper_retraction(paper.doi, paper.publication_types)
        study_type, sample_size = extract_study_metadata_regex(
            paper.title, paper.abstract, paper.publication_types
        )
        quality_weight = calculate_paper_quality_weight(
            study_type, paper.citation_count, paper.year
        )
        stance_info = judge_paper_stance(claim_clean, paper)

        paper_dict = paper.to_dict()
        paper_dict.update({
            "retraction": retraction_status.to_dict(),
            "study_type": study_type,
            "sample_size": sample_size,
            "quality_weight": quality_weight,
            "stance": stance_info["stance"],
            "key_sentence": stance_info["key_sentence"],
            "quote_verified": stance_info["quote_verified"],
            "stance_confidence": stance_info["confidence"],
            "reasoning": stance_info["reasoning"],
        })
        evaluated_papers.append(paper_dict)

    # 5. Consensus & Verdict
    yield sse_message("consensus", "Computing quality-weighted consensus and honest verdict...", 90)
    consensus = compute_consensus(claim_clean, evaluated_papers)

    # 6. Plain-language summary
    yield sse_message("summary", "Drafting plain-language 2-sentence summary...", 95)
    summary = generate_plain_language_summary(consensus)

    consensus["summary"] = summary
    consensus["search_query"] = query
    consensus["is_compound"] = is_compound
    consensus["sub_claims"] = sub_claims
    consensus["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    set_cached_result("claim", claim_clean, consensus)

    yield sse_message("complete", "Verification complete.", 100, result=consensus)
