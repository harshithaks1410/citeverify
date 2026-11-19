"""Centralized prompts for CiteVerify LLM interactions.

All prompts return strict JSON or clear single-line outputs to prevent parsing failures.
"""

QUERY_GENERATION_PROMPT = """You are an academic research librarian specializing in scientific literature retrieval.
Given a scientific claim or user assertion, convert it into an optimal search query of 2 to 5 high-impact medical/scientific keywords suitable for PubMed and Semantic Scholar.
Do NOT include boolean operators, punctuation, quotation marks, or conversational filler.
Return ONLY the search query keywords separated by single spaces.

Example input: "Does intermittent fasting actually help with insulin sensitivity?"
Example output: intermittent fasting insulin sensitivity

Input Claim:
{claim}

Search Query:"""

STANCE_JUDGING_PROMPT = """You are an expert scientific peer-reviewer evaluating whether a published paper's abstract supports, contradicts, or is neutral towards a specific claim.

CLAIM:
"{claim}"

PAPER TITLE:
"{title}"

PAPER ABSTRACT:
"{abstract}"

INSTRUCTIONS:
1. Base your judgment ONLY on the abstract text provided above. Do not assume or extrapolate beyond what is explicitly written.
2. Determine the stance:
   - "supports": The findings/conclusions in the abstract directly back up or provide positive evidence for the claim.
   - "contradicts": The findings/conclusions directly refute, find no effect, or demonstrate the opposite of the claim.
   - "neutral": The abstract is inconclusive, only tangentially related, merely discusses the topic without testing/measuring the claim, or presents equally balanced mixed results.
3. Select ONE exact key sentence from the abstract that best justifies your stance:
   - The sentence MUST be copied VERBATIM directly from the abstract text above.
   - Do NOT edit, summarize, paraphrase, or rephrase this sentence in any way.
   - If no single sentence directly provides evidence, provide an empty string for "key_sentence" and set stance to "neutral".
4. Estimate your confidence (0.0 to 1.0) based strictly on how clearly the abstract addresses the claim.

Return your response in STRICT JSON format with NO markdown wrapping or extraneous text:
{{
  "stance": "supports" | "contradicts" | "neutral",
  "key_sentence": "exact sentence from abstract or empty string",
  "confidence": 0.85,
  "reasoning": "1-2 sentence explanation grounded strictly in the abstract"
}}
"""

STUDY_METADATA_PROMPT = """Analyze the following scientific paper title and abstract to identify the study design and sample size.

TITLE: "{title}"
ABSTRACT: "{abstract}"

Classify study type into EXACTLY one of these categories:
- "meta-analysis"
- "systematic review"
- "randomized controlled trial"
- "clinical trial"
- "cohort study"
- "case-control study"
- "review"
- "in vitro / animal study"
- "other"

Identify sample size if explicitly stated (e.g., "n = 1,420", "n = 45 patients", "120 participants"). If not found, return null.

Return STRICT JSON:
{{
  "study_type": "randomized controlled trial",
  "sample_size": "n = 120"
}}
"""

CLAIM_EXTRACTION_PROMPT = """You are a scientific fact-checker. Extract up to 6 specific, falsifiable scientific, medical, or health claims made in the following article text.

Ignore opinions, journalistic fluff, personal anecdotes, and vague generalities.
Focus only on empirical claims about causes, treatments, health effects, or scientific mechanisms.

ARTICLE TEXT:
{article_text}

Return STRICT JSON in the following schema:
{{
  "claims": [
    {{
      "claim": "Specific testable scientific claim statement",
      "context": "Brief quote or sentence from the article where this claim was made"
    }}
  ]
}}
"""

CLAIM_BREAKDOWN_PROMPT = """You are an analytical philosopher and scientific logician.
Analyze whether the given claim contains multiple distinct, independently verifiable assertions (a compound claim).

Examples:
- "Vitamin C prevents the common cold and cures lung cancer" -> contains 2 claims:
  1. "Vitamin C prevents the common cold"
  2. "Vitamin C treats or cures lung cancer"
- "Intermittent fasting improves insulin sensitivity" -> single atomic claim.

CLAIM:
"{claim}"

If the claim contains multiple distinct assertions, split it into 2 to 4 atomic claims.
If the claim is already a single atomic assertion, return a list containing just the original claim.

Return STRICT JSON:
{{
  "is_compound": true | false,
  "sub_claims": [
    "Sub-claim 1",
    "Sub-claim 2"
  ]
}}
"""

PLAIN_LANGUAGE_SUMMARY_PROMPT = """You are a science communicator explaining research evidence to a curious general reader.
Based strictly on the structured verification results below, write EXACTLY 2 clear, honest, and objective sentences explaining what the scientific consensus shows.

GUIDELINES:
- Do NOT use technical jargon (explain terms like RCT or meta-analysis if referenced).
- Clearly communicate the verdict: whether the evidence supports, contradicts, is mixed, or is too weak/unverifiable.
- Mention the strength or quality of the evidence (e.g. whether it is backed by large clinical trials or only limited/conflicting studies).
- If retracted papers were found, note that flawed or retracted studies were excluded.
- Never make up information not contained in the structured summary.

STRUCTURED VERIFICATION DATA:
- Claim: {claim}
- Final Verdict: {verdict}
- Confidence: {confidence}
- Total usable papers analyzed: {usable_papers_count}
- Quality-weighted Supports: {supports_pct}% ({supports_count} papers)
- Quality-weighted Contradicts: {contradicts_pct}% ({contradicts_count} papers)
- Quality-weighted Neutral: {neutral_pct}% ({neutral_count} papers)
- Retracted papers excluded: {retracted_count}
- Key study types: {study_types}
- Weak evidence flag: {weak_evidence}

Return EXACTLY 2 plain text sentences with no bullet points, no prefixes, and no formatting.
"""
