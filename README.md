# CiteVerify 🔬

> **Free, open-source scientific claim verification web app for researchers, journalists, and curious readers.**  
> Built for a 24-hour hackathon. Zero frameworks, zero build steps, 100% free tech stack.

CiteVerify solves the core flaws of existing academic tools (Consensus, Elicit, Scite):
1. **Never counts votes blindly:** Weights evidence by study design hierarchy ($\text{Meta-Analysis} > \text{Systematic Review} > \text{RCT} > \text{Clinical Trial} > \text{Review} > \text{Observational}$), log-scaled citations, and recency.
2. **Anti-Fabrication Rule:** Unlike generic LLM summaries that hallucinate quotes, CiteVerify verifies every cited sentence against the actual paper abstract in code. Hallucinated or paraphrased quotes are rejected and set to neutral.
3. **Active Retraction Detection:** Every paper DOI is cross-checked against Crossref update notices (`filter=updates:DOI`) and PubMed publication type tags. Retracted papers are highlighted in red and strictly **purged from the consensus score**.
4. **Honest, Explainable Verdicts:** Flags weak evidence when fewer than 3 usable papers exist or study quality is low, capping confidence.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+ (tested on Python 3.10, 3.11, 3.12, 3.13)
- Modern web browser

---

### Setup Instructions

#### Windows (PowerShell)
```powershell
# 1. Clone or navigate to the repository
cd c:\Users\HARSHITHA KS\OneDrive\Desktop\citeverify

# 2. (Optional) Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables (optional for Gemini AI features)
Copy-Item .env.example .env

# 5. Run CiteVerify
python app.py
```

#### Windows (Command Prompt `cmd.exe`)
```cmd
cd "c:\Users\HARSHITHA KS\OneDrive\Desktop\citeverify"
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt
copy .env.example .env
python app.py
```

#### macOS / Linux
```bash
# 1. Navigate to the repository
cd /path/to/citeverify

# 2. (Optional) Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env

# 5. Run CiteVerify
python3 app.py
```

CiteVerify will launch on **http://localhost:5000** and will automatically open your default browser.

---

## 🔑 Environment Configuration (`.env`)

CiteVerify works out-of-the-box with free public APIs (PubMed E-utilities, Crossref, and Semantic Scholar). For AI-powered search keyword extraction, deep abstract stance judging, and natural plain-language summaries, add your free Google Gemini API key:

```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
PORT=5000
HOST=127.0.0.1
```

*Note: The Gemini API key remains securely on the backend and is never exposed to the client browser.*

---

## 🧪 Automated Testing

Run the included verification suite to validate all core anti-fabrication and consensus rules:

```bash
python test_citeverify.py
```

Tests verify:
- ✅ **Anti-fabrication rule:** Verbatim abstract matching accepts authentic quotes and rejects hallucinated/made-up text.
- ✅ **Retraction exclusion:** Retracted papers (e.g. Wakefield MMR study) are completely omitted from consensus tallies and quality weights.
- ✅ **Honest verdicts:** If no papers are found, verdict is strictly `"unverifiable"` with 0% confidence.
- ✅ **Quality weighting:** Meta-analyses receive higher quality weighting than observational reviews.
- ✅ **Study design extraction:** Regex and heuristics correctly extract study types and sample sizes (`n = 1,240`).

---

## 🏗️ Architecture & Modules

CiteVerify is designed with clean separation of concerns:

- `app.py`: Flask application server, error handling (no stack traces exposed), SSE streaming endpoint, Markdown and PDF report generation.
- `pipeline.py`: Orchestrates the end-to-end flow, manages 1-hour in-memory cache, strips article HTML, and yields real-time SSE progress events.
- `search.py`: Parallel search across PubMed E-utilities and Semantic Scholar Graph API, handles HTTP 429 rate limits, and de-duplicates by DOI and normalized title.
- `retraction.py`: Direct Crossref updates API check (`filter=updates:DOI`) and PubMed publication type parser.
- `verify.py`: Gemini client integration, quote verification fuzzy matching, quality weighting formula, and honest verdict logic.
- `prompts.py`: Centralized LLM prompts for query generation, stance judging, article claim extraction, and plain-language summaries.
- `static/`: Pure HTML, CSS, and Vanilla JavaScript (no npm, webpack, or external framework dependencies).

---

## 📊 Quality Weighting Formula

$$\text{Weight} = \text{StudyTypeWeight} \times \text{CitationFactor} \times \text{RecencyFactor}$$

1. **Study Design Multiplier:**
   - Meta-Analysis: **3.0x**
   - Systematic Review: **2.5x**
   - Randomized Controlled Trial (RCT): **2.0x**
   - Clinical Trial: **1.5x**
   - Cohort Study: **1.2x**
   - Narrative Review: **1.0x**
   - In vitro / Animal / Other: **0.8x**

2. **Log-Scaled Citation Factor:**
   $$\text{CitationFactor} = 1.0 + \min(1.5, 0.5 \times \log_{10}(\text{citations} + 1))$$

3. **Recency Factor:**
   - $\le 5$ years: **1.15x**
   - $6 - 10$ years: **1.00x**
   - $11 - 20$ years: **0.90x**
   - $> 20$ years: **0.80x**

---

## 🔒 Privacy & Data Retention

CiteVerify is committed to privacy:
- No database is used.
- User queries, article text, and results are stored **in-memory only** with a 1-hour time-to-live (TTL) cache.
- Nothing is written to permanent disk storage.
