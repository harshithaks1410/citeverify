"""Multi-source scientific paper retrieval module for CiteVerify.

Queries PubMed E-utilities and Semantic Scholar in parallel, de-duplicates
by DOI and normalized title, and filters out papers lacking abstracts.
"""

import concurrent.futures
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "CiteVerify/1.0 (Open-Source Scientific Fact-Checker; mailto:contact@citeverify.org)"
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
REQUEST_TIMEOUT = 12  # seconds


@dataclass
class Paper:
    paper_id: str
    title: str
    abstract: str
    year: Optional[int] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    journal: Optional[str] = None
    citation_count: int = 0
    publication_types: List[str] = field(default_factory=list)
    source: str = "unknown"
    url: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Normalize DOI string for deduplication."""
    if not doi:
        return None
    d = doi.strip().lower()
    d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d)
    d = d.rstrip(".")
    return d if d else None


def normalize_title(title: Optional[str]) -> str:
    """Normalize paper title for fuzzy deduplication."""
    if not title:
        return ""
    # Lowercase and remove punctuation, keep alphanumeric and spaces
    clean = re.sub(r"[^a-z0-9\s]", "", title.lower())
    return " ".join(clean.split())


def search_pubmed(query: str, max_results: int = 8) -> List[Paper]:
    """Search PubMed using E-utilities and parse full XML metadata."""
    papers: List[Paper] = []
    if not query.strip():
        return papers

    headers = {"User-Agent": USER_AGENT}

    try:
        # Step 1: ESearch to obtain PMIDs
        esearch_params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
        }
        res = requests.get(
            PUBMED_ESEARCH_URL,
            params=esearch_params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if res.status_code != 200:
            logger.warning("PubMed ESearch returned status %s", res.status_code)
            return papers

        data = res.json()
        id_list = data.get("esearchresult", {}).get("idlist", [])

        # Specifically check for any retracted publications on the topic
        try:
            retracted_params = {
                "db": "pubmed",
                "term": f"{query} retracted publication[Publication Type]",
                "retmode": "json",
                "retmax": 2,
            }
            ret_res = requests.get(
                PUBMED_ESEARCH_URL,
                params=retracted_params,
                headers=headers,
                timeout=REQUEST_TIMEOUT // 2,
            )
            if ret_res.status_code == 200:
                ret_ids = ret_res.json().get("esearchresult", {}).get("idlist", [])
                for rid in ret_ids:
                    if rid not in id_list:
                        id_list.append(rid)
        except Exception as e:
            logger.debug("Optional retracted search skipped: %s", e)

        if not id_list:
            return papers

        # Step 2: EFetch XML for retrieved PMIDs
        efetch_params = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "xml",
        }
        xml_res = requests.get(
            PUBMED_EFETCH_URL,
            params=efetch_params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if xml_res.status_code != 200:
            logger.warning("PubMed EFetch returned status %s", xml_res.status_code)
            return papers

        root = ET.fromstring(xml_res.content)
        for article in root.findall(".//PubmedArticle"):
            # Title
            title_node = article.find(".//ArticleTitle")
            title = "".join(title_node.itertext()).strip() if title_node is not None else ""
            title = title.rstrip(".")

            # Abstract
            abstract_nodes = article.findall(".//AbstractText")
            abstract_parts = []
            for node in abstract_nodes:
                label = node.attrib.get("Label")
                text = "".join(node.itertext()).strip()
                if text:
                    if label:
                        abstract_parts.append(f"{label}: {text}")
                    else:
                        abstract_parts.append(text)
            abstract = " ".join(abstract_parts).strip()

            if not abstract or len(abstract) < 40:
                continue

            # PMID
            pmid = article.findtext(".//PMID")

            # DOI
            doi = None
            for id_node in article.findall(".//ArticleId"):
                if id_node.attrib.get("IdType") == "doi":
                    doi = normalize_doi(id_node.text)
                    break

            # Year
            year = None
            pub_date_year = article.findtext(".//Journal/JournalIssue/PubDate/Year")
            if not pub_date_year:
                pub_date_year = article.findtext(".//Journal/JournalIssue/PubDate/MedlineDate")
                if pub_date_year:
                    m = re.search(r"\b(19\d\d|20\d\d)\b", pub_date_year)
                    if m:
                        pub_date_year = m.group(1)
            if pub_date_year:
                try:
                    year = int(pub_date_year)
                except ValueError:
                    year = None

            # Authors
            authors = []
            for author_node in article.findall(".//AuthorList/Author"):
                last = author_node.findtext("LastName")
                fore = author_node.findtext("ForeName") or author_node.findtext("Initials")
                if last:
                    authors.append(f"{last} {fore}".strip() if fore else last)

            # Journal
            journal = article.findtext(".//Journal/Title") or article.findtext(".//Journal/ISOAbbreviation")

            # Publication Types
            pub_types = [
                pt.text.strip()
                for pt in article.findall(".//PublicationType")
                if pt.text
            ]

            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (f"https://doi.org/{doi}" if doi else None)

            paper = Paper(
                paper_id=f"pubmed_{pmid}" if pmid else f"pubmed_{len(papers)}",
                title=title,
                abstract=abstract,
                year=year,
                doi=doi,
                pmid=pmid,
                authors=authors[:5],
                journal=journal,
                citation_count=0,
                publication_types=pub_types,
                source="PubMed",
                url=url,
            )
            papers.append(paper)

    except Exception as e:
        logger.error("Error during PubMed search: %s", e)

    return papers


def search_semantic_scholar(query: str, max_results: int = 8) -> List[Paper]:
    """Search Semantic Scholar Graph API with rate-limit retry and fallback."""
    papers: List[Paper] = []
    if not query.strip():
        return papers

    headers = {"User-Agent": USER_AGENT}
    params = {
        "query": query,
        "limit": max_results,
        "fields": "paperId,title,abstract,year,citationCount,publicationTypes,authors,externalIds,url",
    }

    # Up to 2 retries with exponential backoff for 429
    for attempt in range(2):
        try:
            res = requests.get(
                SEMANTIC_SCHOLAR_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if res.status_code == 200:
                data = res.json()
                for item in data.get("data", []):
                    abstract = (item.get("abstract") or "").strip()
                    if not abstract or len(abstract) < 40:
                        continue

                    title = (item.get("title") or "").strip()
                    year = item.get("year")
                    citation_count = item.get("citationCount") or 0
                    external_ids = item.get("externalIds") or {}
                    doi = normalize_doi(external_ids.get("DOI"))
                    pmid = external_ids.get("PubMed")

                    authors = [
                        a.get("name")
                        for a in (item.get("authors") or [])
                        if a.get("name")
                    ][:5]

                    pub_types = item.get("publicationTypes") or []
                    paper_id = item.get("paperId") or ""
                    url = item.get("url") or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None)

                    paper = Paper(
                        paper_id=f"s2_{paper_id}" if paper_id else f"s2_{len(papers)}",
                        title=title,
                        abstract=abstract,
                        year=year,
                        doi=doi,
                        pmid=pmid,
                        authors=authors,
                        journal=None,
                        citation_count=citation_count,
                        publication_types=pub_types,
                        source="Semantic Scholar",
                        url=url,
                    )
                    papers.append(paper)
                break
            elif res.status_code == 429:
                logger.info("Semantic Scholar rate limited (429), backing off (%d/2)...", attempt + 1)
                time.sleep(1.0 + attempt * 1.5)
            else:
                logger.warning("Semantic Scholar returned status %s", res.status_code)
                break
        except Exception as e:
            logger.error("Error during Semantic Scholar search: %s", e)
            break

    return papers


def deduplicate_papers(papers: List[Paper]) -> List[Paper]:
    """De-duplicate papers first by DOI, then by normalized title."""
    seen_dois: Dict[str, Paper] = {}
    seen_titles: Dict[str, Paper] = {}
    deduped: List[Paper] = []

    for paper in papers:
        # Check by DOI
        if paper.doi and paper.doi in seen_dois:
            existing = seen_dois[paper.doi]
            # Merge richer attributes
            if paper.citation_count > existing.citation_count:
                existing.citation_count = paper.citation_count
            for pt in paper.publication_types:
                if pt not in existing.publication_types:
                    existing.publication_types.append(pt)
            if not existing.pmid and paper.pmid:
                existing.pmid = paper.pmid
            if paper.source not in existing.source:
                existing.source = f"{existing.source}, {paper.source}"
            continue

        # Check by normalized title
        norm_title = normalize_title(paper.title)
        if norm_title and norm_title in seen_titles:
            existing = seen_titles[norm_title]
            if not existing.doi and paper.doi:
                existing.doi = paper.doi
            if paper.citation_count > existing.citation_count:
                existing.citation_count = paper.citation_count
            for pt in paper.publication_types:
                if pt not in existing.publication_types:
                    existing.publication_types.append(pt)
            continue

        # New unique paper
        if paper.doi:
            seen_dois[paper.doi] = paper
        if norm_title:
            seen_titles[norm_title] = paper
        deduped.append(paper)

    return deduped


def search_papers(query: str, max_per_source: int = 8) -> List[Paper]:
    """Execute parallel searches across PubMed and Semantic Scholar, deduplicating results."""
    combined_results: List[Paper] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pubmed_future = executor.submit(search_pubmed, query, max_per_source)
        s2_future = executor.submit(search_semantic_scholar, query, max_per_source)

        try:
            pubmed_papers = pubmed_future.result()
            combined_results.extend(pubmed_papers)
        except Exception as e:
            logger.error("PubMed parallel task failed: %s", e)

        try:
            s2_papers = s2_future.result()
            combined_results.extend(s2_papers)
        except Exception as e:
            logger.error("Semantic Scholar parallel task failed: %s", e)

    return deduplicate_papers(combined_results)
