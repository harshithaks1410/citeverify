"""CiteVerify - Free, open-source scientific claim verification web app.

Flask application entry point. Serves the web interface, API endpoints,
and Server-Sent Events (SSE) streaming verification pipeline.
"""

import json
import logging
import os
import threading
import time
import webbrowser
from typing import Any, Dict

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file

from pipeline import (
    extract_text_from_url,
    run_verification_pipeline,
    stream_verification_pipeline,
)
from verify import extract_claims_from_text

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(name)s: %(message)s",
)
logger = logging.getLogger("citeverify")

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates",
)
app.config["JSON_SORT_KEYS"] = False


# ---------------------------------------------------------------------------
# HTTP Error Handlers (Never expose stack traces)
# ---------------------------------------------------------------------------

@app.errorhandler(400)
def handle_bad_request(e):
    return jsonify({
        "error": "Bad Request",
        "message": str(e.description if hasattr(e, "description") else "Invalid request payload.")
    }), 400


@app.errorhandler(404)
def handle_not_found(e):
    return jsonify({
        "error": "Not Found",
        "message": "The requested resource could not be found."
    }), 404


@app.errorhandler(429)
def handle_rate_limit(e):
    return jsonify({
        "error": "Rate Limited",
        "message": "Too many requests to external academic APIs. Please wait a moment and retry."
    }), 429


@app.errorhandler(500)
def handle_server_error(e):
    logger.error("Internal Server Error: %s", e)
    return jsonify({
        "error": "Server Error",
        "message": "An unexpected error occurred while verifying evidence. Please try again."
    }), 500


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the primary single-page UI."""
    return render_template("index.html")


@app.route("/api/verify", methods=["POST"])
def verify_claim_endpoint():
    """Verify a single claim synchronously."""
    data = request.get_json(silent=True) or {}
    claim = (data.get("claim") or "").strip()
    if not claim:
        return jsonify({"error": "Bad Request", "message": "Please provide a non-empty claim string."}), 400

    try:
        result = run_verification_pipeline(claim)
        return jsonify(result)
    except Exception:
        logger.exception("Verification error for claim '%s'", claim)
        return jsonify({
            "error": "Verification Failed",
            "message": "CiteVerify could not complete literature verification for this claim. Please check the claim and try again."
        }), 500


@app.route("/api/verify/stream")
def verify_stream_endpoint():
    """Stream real-time progress and final verification results via Server-Sent Events."""
    claim = (request.args.get("claim") or "").strip()
    if not claim:
        def error_gen():
            yield f"data: {json.dumps({'stage': 'error', 'message': 'Missing claim parameter.'})}\n\n"
        return Response(error_gen(), mimetype="text/event-stream")

    return Response(
        stream_verification_pipeline(claim),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/extract-claims", methods=["POST"])
def extract_claims_endpoint():
    """Extract up to 6 scientific claims from raw article text or an article URL."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    url = (data.get("url") or "").strip()

    if not text and not url:
        return jsonify({"error": "Bad Request", "message": "Provide either 'text' or 'url' in request."}), 400

    try:
        if url:
            if not (url.startswith("http://") or url.startswith("https://")):
                return jsonify({"error": "Bad Request", "message": "Invalid URL protocol. Must start with http:// or https://"}), 400
            try:
                text = extract_text_from_url(url)
            except Exception as e:
                logger.warning("Failed to extract article from URL '%s': %s", url, e)
                return jsonify({
                    "error": "Fetch Error",
                    "message": f"Could not retrieve article content from the provided URL: {str(e)[:100]}"
                }), 400

        if not text or len(text.strip()) < 50:
            return jsonify({
                "error": "Content Insufficient",
                "message": "The article content was too brief or empty to extract scientific claims."
            }), 400

        claims = extract_claims_from_text(text)
        return jsonify({
            "extracted_claims": claims,
            "count": len(claims),
            "source_type": "url" if url else "text",
        })
    except Exception as e:
        logger.error("Error extracting claims: %s", e)
        return jsonify({
            "error": "Extraction Failed",
            "message": "Failed to extract claims from the provided article."
        }), 500


@app.route("/api/export/markdown", methods=["POST"])
def export_markdown_endpoint():
    """Generate and return a clean Markdown evidence report."""
    data = request.get_json(silent=True) or {}
    if not data or "verdict" not in data:
        return jsonify({"error": "Bad Request", "message": "Invalid report payload."}), 400

    claim = data.get("claim", "Scientific Claim")
    verdict = data.get("verdict", "unverifiable").upper()
    confidence = data.get("confidence_label", "none").upper()
    summary = data.get("summary", "")
    timestamp = data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    papers = data.get("papers", [])
    counts = data.get("counts", {})
    percentages = data.get("percentages", {})
    retracted_count = data.get("retracted_papers_count", 0)

    # Build Markdown
    lines = [
        f"# CiteVerify Evidence Report",
        f"**Generated:** {timestamp}",
        f"",
        f"## Investigated Claim",
        f"> {claim}",
        f"",
        f"## Consensus Verdict: **{verdict}** (Confidence: {confidence})",
        f"",
        f"### Summary",
        f"{summary}",
        f"",
        f"### Consensus Breakdown",
        f"- **Supports:** {percentages.get('supports', 0)}% ({counts.get('supports', 0)} papers)",
        f"- **Contradicts:** {percentages.get('contradicts', 0)}% ({counts.get('contradicts', 0)} papers)",
        f"- **Neutral / Inconclusive:** {percentages.get('neutral', 0)}% ({counts.get('neutral', 0)} papers)",
        f"- **Retracted papers excluded:** {retracted_count}",
        f"",
        f"---",
        f"",
        f"## Analyzed Evidence ({len(papers)} Papers)",
        f"",
    ]

    for idx, p in enumerate(papers, 1):
        title = p.get("title", "Untitled")
        url = p.get("url") or (f"https://doi.org/{p.get('doi')}" if p.get("doi") else "#")
        source = p.get("source", "Academic Database")
        year = p.get("year") or "n.d."
        study_type = p.get("study_type", "study").title()
        sample_size = f" ({p.get('sample_size')})" if p.get("sample_size") else ""
        citations = p.get("citation_count", 0)
        quality_weight = p.get("quality_weight", 1.0)
        stance = (p.get("stance") or "neutral").upper()
        retraction = p.get("retraction", {})

        lines.append(f"### {idx}. [{title}]({url})")
        lines.append(f"- **Source:** {source} | **Year:** {year} | **Citations:** {citations}")
        lines.append(f"- **Design:** {study_type}{sample_size} | **Quality Weight:** {quality_weight}x")

        if retraction.get("is_retracted"):
            notice_doi = retraction.get("notice_doi") or "N/A"
            lines.append(f"- **⚠️ RETRACTED PAPER (Excluded from consensus):** {retraction.get('reason')} [Notice DOI: {notice_doi}]")
        else:
            lines.append(f"- **Evaluated Stance:** **{stance}** (Confidence: {p.get('stance_confidence', 0.5):.2f})")

        key_sentence = p.get("key_sentence")
        if key_sentence and p.get("quote_verified"):
            lines.append(f"- **Verified Abstract Quote:** *\"{key_sentence}\"*")
        elif not retraction.get("is_retracted"):
            lines.append(f"- **Evidence Note:** {p.get('reasoning', 'No verbatim quote confirmed in abstract.')}")

        abstract = p.get("abstract", "")
        if abstract:
            lines.append(f"<details><summary>Abstract</summary><p>{abstract}</p></details>")
        lines.append("")

    lines.extend([
        "---",
        "### Limitations & Disclaimer",
        "Verdicts are computed from peer-reviewed study abstracts, not full papers. Always consult full articles before making medical, dietary, or clinical decisions.",
        "CiteVerify is open-source and respects user privacy: queries are held only in temporary in-memory cache and never persisted to disk.",
    ])

    report_content = "\n".join(lines)
    return Response(
        report_content,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="citeverify_report_{int(time.time())}.md"'},
    )


# ---------------------------------------------------------------------------
# Server Startup
# ---------------------------------------------------------------------------

def open_browser_delayed(url: str, delay_seconds: float = 1.0):
    """Open user's default browser after server initialization."""
    def _open():
        time.sleep(delay_seconds)
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.debug("Could not auto-open browser: %s", e)

    threading.Thread(target=_open, daemon=True).start()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "127.0.0.1")
    url = f"http://localhost:{port}"

    logger.info("Starting CiteVerify server at %s", url)
    # Open browser automatically on launch
    open_browser_delayed(url, delay_seconds=1.2)

    app.run(host=host, port=port, debug=False)
