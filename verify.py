"""Verification, stance judging, quote verification, and quality-weighting consensus engine.

Implements the strict ANTI-FABRICATION RULE, study-type quality weighting,
recency and citation scaling, and honest verdict determination.
"""

import datetime
import difflib
import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from prompts import (
    CLAIM_BREAKDOWN_PROMPT,
    CLAIM_EXTRACTION_PROMPT,
    PLAIN_LANGUAGE_SUMMARY_PROMPT,
    QUERY_GENERATION_PROMPT,
    STANCE_JUDGING_PROMPT,
    STUDY_METADATA_PROMPT,
)
from retraction import RetractionStatus, verify_paper_retraction
from search import Paper

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

_gemini_client = None


def get_gemini_client():
    """Lazily initialize Google GenAI client."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from google import genai
        _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client
    except Exception as e:
        logger.error("Failed to initialize Google GenAI client: %s", e)
        return None


# ---------------------------------------------------------------------------
# 1. Query Generation
# ---------------------------------------------------------------------------

def generate_search_query(claim: str) -> str:
    """Convert claim to 2-5 high-impact academic search keywords. Falls back to heuristic extraction."""
    if not claim or not claim.strip():
        return ""

    client = get_gemini_client()
    if client:
        try:
            from google.genai import types
            prompt = QUERY_GENERATION_PROMPT.format(claim=claim.strip())
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=60,
                ),
            )
            raw = (response.text or "").strip()
            # Clean up output
            raw = re.sub(r'["\n\r]', ' ', raw)
            words = [w for w in raw.split() if w.isalnum()]
            if words and 1 <= len(words) <= 8:
                return " ".join(words)
        except Exception as e:
            logger.warning("Gemini query generation failed: %s; falling back to heuristic", e)

    # Heuristic fallback: remove common English stopwords and punctuation
    stopwords = {
        "does", "do", "is", "are", "was", "were", "can", "could", "will", "would",
        "should", "a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "by",
        "for", "with", "about", "against", "between", "into", "through", "during",
        "before", "after", "above", "below", "from", "up", "down", "in", "out",
        "that", "this", "these", "those", "i", "you", "he", "she", "it", "we", "they",
        "really", "actually", "cause", "causes", "causing", "improve", "improves",
        "prevent", "prevents", "help", "helps", "reduce", "reduces"
    }
    clean = re.sub(r"[^a-zA-Z0-9\s]", " ", claim)
    tokens = clean.split()
    meaningful = [t for t in tokens if t.lower() not in stopwords]
    if len(meaningful) >= 2:
        return " ".join(meaningful[:6])
    return " ".join(tokens[:6])


# ---------------------------------------------------------------------------
# 2. Quote Verification (ANTI-FABRICATION RULE)
# ---------------------------------------------------------------------------

def normalize_text_for_matching(text: str) -> str:
    """Normalize text by lowercasing, removing all non-alphanumeric chars, and collapsing whitespace."""
    if not text:
        return ""
    clean = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return " ".join(clean.split())


def verify_quote_in_abstract(key_sentence: Optional[str], abstract: str) -> Tuple[bool, Optional[str]]:
    """ANTI-FABRICATION RULE: Verify in code that key_sentence actually appears in abstract.

    Uses case/whitespace-insensitive fuzzy substring and token matching.
    Returns (is_verified, verified_sentence_or_none).
    """
    if not key_sentence or not key_sentence.strip() or not abstract:
        return False, None

    norm_quote = normalize_text_for_matching(key_sentence)
    norm_abstract = normalize_text_for_matching(abstract)

    if not norm_quote:
        return False, None

    # Step A: Exact normalized substring match
    if norm_quote in norm_abstract:
        return True, key_sentence.strip()

    # Step B: If sentence was slightly truncated or has trailing words, check if 80%+ of quote is in abstract
    quote_words = norm_quote.split()
    if len(quote_words) >= 4:
        # Check consecutive 5-word shingles
        shingle_size = min(len(quote_words), 5)
        matches = 0
        total_shingles = len(quote_words) - shingle_size + 1
        for i in range(total_shingles):
            shingle = " ".join(quote_words[i : i + shingle_size])
            if shingle in norm_abstract:
                matches += 1
        if total_shingles > 0 and (matches / total_shingles) >= 0.75:
            return True, key_sentence.strip()

    # Step C: Diff similarity against abstract sentences
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    for s in sentences:
        norm_s = normalize_text_for_matching(s)
        if norm_s:
            ratio = difflib.SequenceMatcher(None, norm_quote, norm_s).ratio()
            if ratio >= 0.82:
                return True, s.strip()

    return False, None


# ---------------------------------------------------------------------------
# 3. Study Type and Sample Size Extraction
# ---------------------------------------------------------------------------

STUDY_TYPE_WEIGHTS = {
    "meta-analysis": 3.0,
    "systematic review": 2.5,
    "randomized controlled trial": 2.0,
    "clinical trial": 1.5,
    "cohort study": 1.2,
    "case-control study": 1.1,
    "review": 1.0,
    "in vitro / animal study": 0.8,
    "other": 0.8,
}


def extract_study_metadata_regex(title: str, abstract: str, pub_types: List[str]) -> Tuple[str, Optional[str]]:
    """Detect study design and sample size using deterministic pattern matching."""
    text_corpus = f"{title} {abstract}".lower()
    types_lower = [pt.lower() for pt in pub_types]

    # Detect study type
    study_type = "other"
    if any("meta-analysis" in pt for pt in types_lower) or "meta-analysis" in text_corpus or "systematic review and meta-analysis" in text_corpus:
        study_type = "meta-analysis"
    elif any("systematic review" in pt for pt in types_lower) or "systematic review" in text_corpus:
        study_type = "systematic review"
    elif any("randomized controlled trial" in pt for pt in types_lower) or "randomized controlled trial" in text_corpus or "rct" in text_corpus:
        study_type = "randomized controlled trial"
    elif any("clinical trial" in pt for pt in types_lower) or "clinical trial" in text_corpus:
        study_type = "clinical trial"
    elif "cohort study" in text_corpus or "prospective cohort" in text_corpus:
        study_type = "cohort study"
    elif "case-control" in text_corpus:
        study_type = "case-control study"
    elif any("review" in pt for pt in types_lower) or "review" in text_corpus:
        study_type = "review"
    elif any(term in text_corpus for term in ["in vitro", "mice", "murine", "rats", "animal model"]):
        study_type = "in vitro / animal study"

    # Detect sample size
    sample_size = None
    sample_patterns = [
        r"\b(?:n|N)\s*=\s*([0-9,]+)\b",
        r"\b([0-9,]+)\s*(?:patients|participants|subjects|cases|individuals|volunteers|adults|children|women|men)\b",
        r"\bcohort\s*of\s*([0-9,]+)\b",
        r"\bsample\s*size\s*of\s*([0-9,]+)\b",
    ]
    for pattern in sample_patterns:
        m = re.search(pattern, abstract, re.IGNORECASE)
        if m:
            num_str = m.group(1).replace(",", "")
            try:
                num = int(num_str)
                if 2 <= num <= 5000000:
                    sample_size = f"n = {num:,}"
                    break
            except ValueError:
                pass

    return study_type, sample_size


# ---------------------------------------------------------------------------
# 4. Stance Judging (Abstract Level only)
# ---------------------------------------------------------------------------

def judge_paper_stance_heuristic(claim: str, abstract: str) -> Dict[str, Any]:
    """Heuristic fallback for stance judgment when Gemini API key is unavailable."""
    abstract_lower = abstract.lower()
    claim_terms = [t.lower() for t in re.sub(r"[^a-zA-Z0-9\s]", " ", claim).split() if len(t) > 3]

    contradict_markers = [
        "no significant difference", "failed to show", "did not prevent",
        "no evidence", "no effect", "did not improve", "ineffective",
        "contradicts", "refutes", "no association", "not associated",
        "no protective effect", "lack of efficacy", "increased the risk",
        "did not reduce", "no relationship"
    ]
    support_markers = [
        "significantly improved", "demonstrated efficacy", "effective in",
        "reduced the risk", "reduces the risk", "supports the use", "beneficial effect",
        "showed a significant reduction", "positive correlation", "protective effect",
        "associated with lower risk", "inversely associated", "decreased risk",
        "alleviates depressive", "antidepressant effect", "reduction in depressive",
        "prevent depression", "lowering the risk", "beneficial in reducing",
        "effective intervention", "improved outcomes", "significantly lower"
    ]

    has_contra = any(m in abstract_lower for m in contradict_markers)
    has_supp = any(m in abstract_lower for m in support_markers)

    # Find the most relevant sentence in abstract
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    best_sentence = ""
    for s in sentences:
        s_lower = s.lower()
        if (has_contra and any(m in s_lower for m in contradict_markers)) or \
           (has_supp and any(m in s_lower for m in support_markers)):
            best_sentence = s.strip()
            break

    if has_contra and not has_supp:
        return {
            "stance": "contradicts",
            "key_sentence": best_sentence,
            "confidence": 0.70,
            "reasoning": "Abstract reports negative findings or lack of significant efficacy."
        }
    elif has_supp and not has_contra:
        return {
            "stance": "supports",
            "key_sentence": best_sentence,
            "confidence": 0.75,
            "reasoning": "Abstract reports positive efficacy or significant improvements."
        }
    else:
        return {
            "stance": "neutral",
            "key_sentence": best_sentence,
            "confidence": 0.50,
            "reasoning": "Abstract presents mixed, observational, or non-definitive findings."
        }


def judge_paper_stance(claim: str, paper: Paper) -> Dict[str, Any]:
    """Judge stance from abstract using Gemini LLM with retry, strict JSON, and anti-fabrication enforcement."""
    client = get_gemini_client()
    raw_stance_data: Optional[Dict[str, Any]] = None

    if client:
        prompt = STANCE_JUDGING_PROMPT.format(
            claim=claim,
            title=paper.title,
            abstract=paper.abstract,
        )

        # Retry up to 2 times for strict JSON parsing
        for attempt in range(2):
            try:
                from google.genai import types
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        response_mime_type="application/json",
                    ),
                )
                text = (response.text or "").strip()
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "stance" in parsed:
                    raw_stance_data = parsed
                    break
            except Exception as e:
                logger.warning("Gemini stance judging attempt %d failed: %s", attempt + 1, e)

    if not raw_stance_data:
        raw_stance_data = judge_paper_stance_heuristic(claim, paper.abstract)

    # Sanitize stance
    stance = str(raw_stance_data.get("stance", "neutral")).lower().strip()
    if stance not in {"supports", "contradicts", "neutral"}:
        stance = "neutral"

    raw_quote = raw_stance_data.get("key_sentence") or ""
    confidence = float(raw_stance_data.get("confidence", 0.7))
    reasoning = str(raw_stance_data.get("reasoning", ""))

    # -------------------------------------------------------------
    # ANTI-FABRICATION RULE VERIFICATION
    # -------------------------------------------------------------
    quote_is_verified, verified_quote = verify_quote_in_abstract(raw_quote, paper.abstract)

    if not quote_is_verified and raw_quote.strip():
        # Discard fabricated or hallucinated quote and set stance to neutral
        logger.warning(
            "ANTI-FABRICATION: Discarding unverified key sentence for paper '%s'. Quote was: '%s'",
            paper.title[:40], raw_quote[:60]
        )
        return {
            "stance": "neutral",
            "key_sentence": None,
            "quote_verified": False,
            "confidence": 0.4,
            "reasoning": "Evidence unclear: Cited key sentence was not found verbatim in abstract."
        }

    return {
        "stance": stance,
        "key_sentence": verified_quote if quote_is_verified else None,
        "quote_verified": quote_is_verified,
        "confidence": confidence,
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# 5. Quality Weighting & Consensus Calculation
# ---------------------------------------------------------------------------

def calculate_paper_quality_weight(
    study_type: str,
    citation_count: int,
    year: Optional[int],
) -> float:
    """Calculate quality weight based on study design, citation count, and recency."""
    current_year = datetime.datetime.now().year

    # 1. Study design weight
    study_weight = STUDY_TYPE_WEIGHTS.get(study_type.lower(), 0.8)

    # 2. Citation weight (log-scaled)
    # 0 citations -> 1.0; 10 citations -> 1.5; 100 citations -> 2.0; 1000+ -> 2.5
    citations = max(0, citation_count)
    citation_factor = 1.0 + min(1.5, math.log10(citations + 1) * 0.5)

    # 3. Recency factor
    if year:
        age = max(0, current_year - year)
        if age <= 5:
            recency_factor = 1.15
        elif age <= 10:
            recency_factor = 1.0
        elif age <= 20:
            recency_factor = 0.9
        else:
            recency_factor = 0.8
    else:
        recency_factor = 0.95

    return round(study_weight * citation_factor * recency_factor, 2)


def compute_consensus(
    claim: str,
    evaluated_papers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compute quality-weighted consensus, verdict, honest confidence, and weak evidence flags.

    Retracted papers are strictly EXCLUDED from consensus calculations.
    """
    total_papers = len(evaluated_papers)
    retracted_papers = [p for p in evaluated_papers if p.get("retraction", {}).get("is_retracted")]
    retracted_count = len(retracted_papers)

    # Usable papers: non-retracted only
    usable_papers = [p for p in evaluated_papers if not p.get("retraction", {}).get("is_retracted")]
    usable_count = len(usable_papers)

    # Initialize tallies
    supports_count = sum(1 for p in usable_papers if p["stance"] == "supports")
    contradicts_count = sum(1 for p in usable_papers if p["stance"] == "contradicts")
    neutral_count = sum(1 for p in usable_papers if p["stance"] == "neutral")

    supports_weighted = sum(p["quality_weight"] for p in usable_papers if p["stance"] == "supports")
    contradicts_weighted = sum(p["quality_weight"] for p in usable_papers if p["stance"] == "contradicts")
    neutral_weighted = sum(p["quality_weight"] for p in usable_papers if p["stance"] == "neutral")

    total_weighted = supports_weighted + contradicts_weighted + neutral_weighted

    if total_weighted > 0:
        supports_pct = round((supports_weighted / total_weighted) * 100, 1)
        contradicts_pct = round((contradicts_weighted / total_weighted) * 100, 1)
        neutral_pct = round((neutral_weighted / total_weighted) * 100, 1)
    else:
        supports_pct = contradicts_pct = neutral_pct = 0.0

    # -------------------------------------------------------------
    # Honest Verdict Rules
    # -------------------------------------------------------------
    weak_evidence = False
    weak_evidence_reasons = []

    if usable_count == 0:
        verdict = "unverifiable"
        confidence_score = 0.0
        confidence_label = "none"
        weak_evidence = True
        weak_evidence_reasons.append("No usable peer-reviewed papers with abstracts found.")
    else:
        if usable_count < 3:
            weak_evidence = True
            weak_evidence_reasons.append(f"Fewer than 3 usable papers found (analyzed {usable_count}).")

        # Check if high-quality studies exist
        high_quality_count = sum(
            1 for p in usable_papers
            if p.get("study_type") in {"meta-analysis", "systematic review", "randomized controlled trial"}
        )
        if usable_count >= 3 and high_quality_count == 0:
            weak_evidence = True
            weak_evidence_reasons.append("Evidence comes primarily from observational or low-tier studies.")

        # Determine verdict category
        decisive_weight = supports_weighted + contradicts_weighted
        if decisive_weight == 0:
            verdict = "unverifiable"
            confidence_score = 0.25
            confidence_label = "low"
            weak_evidence = True
            weak_evidence_reasons.append("All retrieved studies were neutral or inconclusive regarding this specific claim.")
        else:
            supp_share = supports_weighted / decisive_weight
            contra_share = contradicts_weighted / decisive_weight

            if supp_share >= 0.65 and supports_weighted >= 1.8 * contradicts_weighted:
                verdict = "supported"
            elif contra_share >= 0.65 and contradicts_weighted >= 1.8 * supports_weighted:
                verdict = "contradicted"
            elif supports_count > 0 and contradicts_count > 0:
                verdict = "mixed"
            else:
                verdict = "mixed"

        # Calculate confidence score honestly
        # Scale with number of usable papers and clarity of consensus
        paper_count_factor = min(1.0, usable_count / 6.0)
        margin = abs(supports_pct - contradicts_pct) / 100.0
        raw_conf = (0.4 * paper_count_factor) + (0.4 * margin) + (0.2 * (high_quality_count / max(1, usable_count)))

        if weak_evidence:
            # Cap confidence strictly on thin evidence
            confidence_score = min(0.48, round(raw_conf, 2))
            confidence_label = "low"
        else:
            confidence_score = round(min(0.95, max(0.40, raw_conf)), 2)
            if confidence_score >= 0.75:
                confidence_label = "high"
            elif confidence_score >= 0.50:
                confidence_label = "moderate"
            else:
                confidence_label = "low"

    # Compile study types summary
    study_type_counts: Dict[str, int] = {}
    for p in usable_papers:
        st = p.get("study_type", "other")
        study_type_counts[st] = study_type_counts.get(st, 0) + 1
    study_types_str = ", ".join(f"{k} ({v})" for k, v in study_type_counts.items()) or "None"

    return {
        "claim": claim,
        "verdict": verdict,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "weak_evidence": weak_evidence,
        "weak_evidence_message": " ".join(weak_evidence_reasons) if weak_evidence else None,
        "total_papers": total_papers,
        "usable_papers_count": usable_count,
        "retracted_papers_count": retracted_count,
        "counts": {
            "supports": supports_count,
            "contradicts": contradicts_count,
            "neutral": neutral_count,
        },
        "quality_weighted_scores": {
            "supports": round(supports_weighted, 2),
            "contradicts": round(contradicts_weighted, 2),
            "neutral": round(neutral_weighted, 2),
            "total": round(total_weighted, 2),
        },
        "percentages": {
            "supports": supports_pct,
            "contradicts": contradicts_pct,
            "neutral": neutral_pct,
        },
        "study_types_summary": study_types_str,
        "papers": evaluated_papers,
    }


# ---------------------------------------------------------------------------
# 6. Plain-Language Summary Generator
# ---------------------------------------------------------------------------

def generate_plain_language_summary(consensus_data: Dict[str, Any]) -> str:
    """Generate exactly 2 sentences explaining the verdict for general readers."""
    client = get_gemini_client()
    if client:
        try:
            from google.genai import types
            prompt = PLAIN_LANGUAGE_SUMMARY_PROMPT.format(
                claim=consensus_data["claim"],
                verdict=consensus_data["verdict"].upper(),
                confidence=consensus_data["confidence_label"].upper(),
                usable_papers_count=consensus_data["usable_papers_count"],
                supports_pct=consensus_data["percentages"]["supports"],
                supports_count=consensus_data["counts"]["supports"],
                contradicts_pct=consensus_data["percentages"]["contradicts"],
                contradicts_count=consensus_data["counts"]["contradicts"],
                neutral_pct=consensus_data["percentages"]["neutral"],
                neutral_count=consensus_data["counts"]["neutral"],
                retracted_count=consensus_data["retracted_papers_count"],
                study_types=consensus_data["study_types_summary"],
                weak_evidence=consensus_data["weak_evidence"],
            )
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=150,
                ),
            )
            summary = (response.text or "").strip()
            if summary and len(summary) > 20:
                return summary
        except Exception as e:
            logger.warning("Gemini plain-language summary failed: %s; falling back", e)

    # Deterministic fallback template
    v = consensus_data["verdict"]
    cnt = consensus_data["usable_papers_count"]
    ret_cnt = consensus_data["retracted_papers_count"]

    ret_clause = f" Note that {ret_cnt} retracted paper was excluded from this evaluation." if ret_cnt == 1 else (
        f" Note that {ret_cnt} retracted papers were excluded from this evaluation." if ret_cnt > 1 else ""
    )

    if v == "supported":
        s1 = f"Current scientific literature generally supports this claim across {cnt} analyzed peer-reviewed studies."
        s2 = f"Positive findings outweigh contrary evidence with {consensus_data['confidence_label']} confidence based on study quality.{ret_clause}"
    elif v == "contradicted":
        s1 = f"Scientific evidence directly contradicts this claim across {cnt} analyzed peer-reviewed studies."
        s2 = f"Controlled studies report no significant benefit or refute the assertion with {consensus_data['confidence_label']} confidence.{ret_clause}"
    elif v == "mixed":
        s1 = f"Scientific findings regarding this claim are currently mixed across {cnt} peer-reviewed studies."
        s2 = f"Studies show conflicting outcomes, suggesting effects may depend heavily on specific conditions or populations.{ret_clause}"
    else:
        s1 = "There is insufficient or inconclusive peer-reviewed evidence to substantiate or refute this claim."
        s2 = f"Available studies do not provide direct, high-quality verification.{ret_clause}"

    return f"{s1} {s2}"


# ---------------------------------------------------------------------------
# 7. Claim Breakdown (Multi-part claims)
# ---------------------------------------------------------------------------

def breakdown_compound_claim(claim: str) -> List[str]:
    """Breakdown a claim into sub-claims if it contains multiple assertions."""
    if not claim or not claim.strip():
        return []

    client = get_gemini_client()
    if client:
        try:
            from google.genai import types
            prompt = CLAIM_BREAKDOWN_PROMPT.format(claim=claim.strip())
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            parsed = json.loads(response.text)
            sub_claims = parsed.get("sub_claims", [])
            if sub_claims and isinstance(sub_claims, list) and len(sub_claims) > 1:
                return [s.strip() for s in sub_claims if s.strip()]
        except Exception as e:
            logger.warning("Gemini claim breakdown failed: %s; using heuristic", e)

    # Heuristic check for "and" / "while" conjunctions
    conjunction_pattern = r"\s+(?:and|while|as\s+well\s+as)\s+"
    parts = re.split(conjunction_pattern, claim, flags=re.IGNORECASE)
    if len(parts) > 1 and all(len(p.strip().split()) >= 3 for p in parts):
        return [p.strip().capitalize() for p in parts if p.strip()]

    return [claim.strip()]


# ---------------------------------------------------------------------------
# 8. Article Claim Extraction
# ---------------------------------------------------------------------------

def extract_claims_from_text(article_text: str) -> List[Dict[str, str]]:
    """Extract up to 6 checkable scientific claims from article text."""
    if not article_text or not article_text.strip():
        return []

    client = get_gemini_client()
    if client:
        try:
            from google.genai import types
            # Truncate article if excessively long to stay well within token bounds
            truncated_text = article_text.strip()[:6000]
            prompt = CLAIM_EXTRACTION_PROMPT.format(article_text=truncated_text)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            parsed = json.loads(response.text)
            claims = parsed.get("claims", [])
            if claims and isinstance(claims, list):
                return claims[:6]
        except Exception as e:
            logger.warning("Gemini claim extraction failed: %s; falling back", e)

    # Heuristic fallback: look for sentences with health/medical assertion verbs
    sentences = re.split(r"(?<=[.!?])\s+", article_text.strip())
    assertion_patterns = [
        r"\b(?:improves?|reduces?|increases?|prevents?|causes?|treats?|links?|associated\s+with)\b",
        r"\b(?:risk\s+of|protects\s+against|effective\s+for|leads\s+to)\b",
    ]
    extracted = []
    for s in sentences:
        s_clean = s.strip()
        if 25 <= len(s_clean) <= 220:
            if any(re.search(pat, s_clean, re.IGNORECASE) for pat in assertion_patterns):
                extracted.append({"claim": s_clean, "context": s_clean})
                if len(extracted) >= 6:
                    break

    return extracted
