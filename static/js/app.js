/**
 * CiteVerify Client Application
 * Plain JavaScript, zero frameworks, keyboard accessible.
 */

document.addEventListener("DOMContentLoaded", () => {
  // Global state
  let currentResult = null;
  let activeEventSource = null;

  // DOM Elements
  const tabButtons = document.querySelectorAll(".tab-button");
  const tabPanels = document.querySelectorAll(".tab-panel");

  const formClaim = document.getElementById("form-claim");
  const claimInput = document.getElementById("claim-input");
  const btnVerifyClaim = document.getElementById("btn-verify-claim");

  const formText = document.getElementById("form-text");
  const articleTextInput = document.getElementById("article-text-input");
  const btnExtractText = document.getElementById("btn-extract-text");

  const formUrl = document.getElementById("form-url");
  const articleUrlInput = document.getElementById("article-url-input");
  const btnExtractUrl = document.getElementById("btn-extract-url");

  const progressCard = document.getElementById("progress-card");
  const progressFill = document.getElementById("progress-fill");
  const progressPct = document.getElementById("progress-pct");
  const progressStatusText = document.getElementById("progress-status-text");
  const progressLog = document.getElementById("progress-log");

  const errorBox = document.getElementById("error-box");
  const errorTitle = document.getElementById("error-title");
  const errorMessage = document.getElementById("error-message");

  const extractedClaimsSection = document.getElementById("extracted-claims-section");
  const extractedClaimsList = document.getElementById("extracted-claims-list");
  const btnCloseClaims = document.getElementById("btn-close-claims");

  const resultsSection = document.getElementById("results-section");
  const compoundBreakdownBox = document.getElementById("compound-breakdown-box");
  const subClaimsChips = document.getElementById("sub-claims-chips");

  const verdictBanner = document.getElementById("verdict-banner");
  const verdictIcon = document.getElementById("verdict-icon");
  const verdictTitle = document.getElementById("verdict-title");
  const verdictConfidence = document.getElementById("verdict-confidence");
  const verdictPaperCount = document.getElementById("verdict-paper-count");
  const verdictSummary = document.getElementById("verdict-summary");
  const weakEvidenceBox = document.getElementById("weak-evidence-box");
  const weakEvidenceText = document.getElementById("weak-evidence-text");

  const barSupports = document.getElementById("bar-supports");
  const barContradicts = document.getElementById("bar-contradicts");
  const barNeutral = document.getElementById("bar-neutral");
  const pctSupports = document.getElementById("pct-supports");
  const pctContradicts = document.getElementById("pct-contradicts");
  const pctNeutral = document.getElementById("pct-neutral");
  const cntSupports = document.getElementById("cnt-supports");
  const cntContradicts = document.getElementById("cnt-contradicts");
  const cntNeutral = document.getElementById("cnt-neutral");
  const legendRetractedItem = document.getElementById("legend-retracted-item");
  const cntRetracted = document.getElementById("cnt-retracted");

  const filterStance = document.getElementById("filter-stance");
  const sortPapers = document.getElementById("sort-papers");
  const papersList = document.getElementById("papers-list");

  const btnExportMd = document.getElementById("btn-export-md");
  const btnExportPdf = document.getElementById("btn-export-pdf");

  // -------------------------------------------------------------------------
  // Tabs Navigation
  // -------------------------------------------------------------------------
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabButtons.forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-selected", "false");
      });
      tabPanels.forEach((p) => {
        p.classList.remove("active");
        p.setAttribute("hidden", "true");
      });

      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      const targetId = btn.getAttribute("data-tab");
      const targetPanel = document.getElementById(targetId);
      if (targetPanel) {
        targetPanel.classList.add("active");
        targetPanel.removeAttribute("hidden");
      }
    });
  });

  // -------------------------------------------------------------------------
  // Example Claims Chips
  // -------------------------------------------------------------------------
  document.querySelectorAll(".demo-buttons .btn-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const claim = chip.getAttribute("data-claim");
      if (claim) {
        // Switch to Claim tab
        const claimTabBtn = document.getElementById("tab-btn-claim");
        if (claimTabBtn) claimTabBtn.click();
        claimInput.value = claim;
        verifyClaimWithProgress(claim);
      }
    });
  });

  // -------------------------------------------------------------------------
  // Claim Verification Form (SSE Streaming + Fallback)
  // -------------------------------------------------------------------------
  formClaim.addEventListener("submit", (e) => {
    e.preventDefault();
    const claim = claimInput.value.trim();
    if (!claim) return;
    verifyClaimWithProgress(claim);
  });

  function showError(title, msg) {
    hideProgress();
    errorTitle.textContent = title;
    errorMessage.textContent = msg;
    errorBox.removeAttribute("hidden");
    errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function hideError() {
    errorBox.setAttribute("hidden", "true");
  }

  function showProgress() {
    hideError();
    resultsSection.setAttribute("hidden", "true");
    progressCard.removeAttribute("hidden");
    progressFill.style.width = "5%";
    progressPct.textContent = "5%";
    progressStatusText.textContent = "Connecting to verification pipeline...";
    progressLog.textContent = "";
    btnVerifyClaim.disabled = true;
    progressCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function updateProgress(pct, text, logLine) {
    progressFill.style.width = `${pct}%`;
    progressPct.textContent = `${pct}%`;
    progressStatusText.textContent = text;
    if (logLine) {
      progressLog.textContent = logLine;
    }
  }

  function hideProgress() {
    progressCard.setAttribute("hidden", "true");
    btnVerifyClaim.disabled = false;
  }

  function verifyClaimWithProgress(claim) {
    hideError();

    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }

    showProgress();

    // Use Server-Sent Events stream for live status
    const streamUrl = `/api/verify/stream?claim=${encodeURIComponent(claim)}`;
    let eventSource;
    try {
      eventSource = new EventSource(streamUrl);
      activeEventSource = eventSource;
    } catch (err) {
      console.warn("EventSource unavailable, using fetch fallback:", err);
      verifyClaimFallback(claim);
      return;
    }

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.stage === "error") {
          eventSource.close();
          showError("Verification Notice", data.message || "Pipeline error.");
          return;
        }

        if (data.progress !== undefined) {
          updateProgress(
            data.progress,
            data.message || "Processing...",
            data.paper_title || data.query || ""
          );
        }

        if (data.stage === "complete" && data.result) {
          eventSource.close();
          activeEventSource = null;
          hideProgress();
          renderResults(data.result);
          hideError();
        }
      } catch (err) {
        console.error("Error processing SSE verification update:", err);
      }
    };

    eventSource.onerror = (err) => {
      console.warn("SSE connection closed or errored, attempting fallback:", err);
      eventSource.close();
      activeEventSource = null;
      // Fallback to standard POST request if stream drops
      verifyClaimFallback(claim);
    };
  }

  async function verifyClaimFallback(claim) {
    hideError();
    let res;
    let data;

    try {
      updateProgress(50, "Fetching literature via API...", "");
      res = await fetch("/api/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ claim }),
      });
      data = await res.json();
    } catch (err) {
      console.error("Verification request failed:", err);
      showError("Network Error", "Unable to reach CiteVerify backend. Ensure the server is running.");
      return;
    }

    if (!res.ok) {
      showError(data.error || "Verification Error", data.message || "Failed to verify claim.");
      return;
    }

    hideProgress();
    hideError();
    try {
      renderResults(data);
    } catch (err) {
      console.error("Error rendering verification results:", err);
    } finally {
      hideError();
    }
  }

  // -------------------------------------------------------------------------
  // Render Verification Results
  // -------------------------------------------------------------------------
  function renderResults(data) {
    currentResult = data;
    hideError();

    // 1. Compound claim notice
    try {
      const subClaims = Array.isArray(data.sub_claims)
        ? data.sub_claims.filter((sc) => typeof sc === "string" && sc.trim())
        : [];
      if (data.is_compound && subClaims.length > 1) {
        compoundBreakdownBox.removeAttribute("hidden");
        subClaimsChips.innerHTML = "";
        subClaims.forEach((sc) => {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "sub-claim-chip";
          chip.textContent = sc;
          chip.title = "Click to verify this individual sub-claim";
          chip.addEventListener("click", () => {
            claimInput.value = sc;
            verifyClaimWithProgress(sc);
          });
          subClaimsChips.appendChild(chip);
        });
      } else {
        subClaimsChips.innerHTML = "";
        compoundBreakdownBox.setAttribute("hidden", "true");
      }
    } catch (err) {
      console.error("Error rendering compound-claim sub-claims:", err);
      compoundBreakdownBox.setAttribute("hidden", "true");
    }

    // 2. Verdict Banner styling
    verdictBanner.className = "verdict-banner";
    const v = (data.verdict || "unverifiable").toLowerCase();
    verdictBanner.classList.add(`verdict-${v}`);

    const verdictIcons = {
      supported: "✅",
      contradicted: "❌",
      mixed: "⚖️",
      unverifiable: "❓",
    };
    verdictIcon.textContent = verdictIcons[v] || "❓";
    verdictTitle.textContent = v.toUpperCase();

    // Confidence
    const conf = (data.confidence_label || "low").toUpperCase();
    verdictConfidence.textContent = `${conf} (${Math.round((data.confidence_score || 0) * 100)}%)`;
    verdictPaperCount.textContent = data.usable_papers_count || 0;

    // Plain-Language Summary
    verdictSummary.textContent = data.summary || "Summary unavailable.";

    // Weak Evidence Banner
    if (data.weak_evidence && data.weak_evidence_message) {
      weakEvidenceBox.removeAttribute("hidden");
      weakEvidenceText.textContent = data.weak_evidence_message;
    } else {
      weakEvidenceBox.setAttribute("hidden", "true");
    }

    // 3. Consensus Meter Stacked Bar
    const percentages = data.percentages || {};
    const counts = data.counts || {};
    const pSupp = percentages.supports || 0;
    const pContra = percentages.contradicts || 0;
    const pNeut = percentages.neutral || 0;

    barSupports.style.width = `${pSupp}%`;
    barContradicts.style.width = `${pContra}%`;
    barNeutral.style.width = `${pNeut}%`;

    pctSupports.textContent = `${pSupp}%`;
    pctContradicts.textContent = `${pContra}%`;
    pctNeutral.textContent = `${pNeut}%`;

    cntSupports.textContent = counts.supports || 0;
    cntContradicts.textContent = counts.contradicts || 0;
    cntNeutral.textContent = counts.neutral || 0;

    // Retracted count in legend
    if (data.retracted_papers_count > 0) {
      legendRetractedItem.removeAttribute("hidden");
      cntRetracted.textContent = data.retracted_papers_count;
    } else {
      legendRetractedItem.setAttribute("hidden", "true");
    }

    // 4. Render Papers
    renderPapersListSafely();

    // Show Results Section
    resultsSection.removeAttribute("hidden");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
    hideError();
  }

  // -------------------------------------------------------------------------
  // Render Papers List with Filter & Sort
  // -------------------------------------------------------------------------
  function renderPapersListSafely() {
    try {
      renderPapersList();
    } catch (err) {
      console.error("Error rendering evidence cards:", err);
    }
  }

  function renderPapersList() {
    if (!currentResult || !Array.isArray(currentResult.papers)) {
      papersList.innerHTML = "";
      return;
    }

    const filterVal = filterStance.value;
    const sortVal = sortPapers.value;

    let papers = [...currentResult.papers];

    // Filter
    if (filterVal !== "all") {
      if (filterVal === "retracted") {
        papers = papers.filter((p) => p.retraction && p.retraction.is_retracted);
      } else {
        papers = papers.filter((p) => !p.retraction?.is_retracted && p.stance === filterVal);
      }
    }

    // Sort
    papers.sort((a, b) => {
      if (sortVal === "weight") {
        return (b.quality_weight || 0) - (a.quality_weight || 0);
      } else if (sortVal === "citations") {
        return (b.citation_count || 0) - (a.citation_count || 0);
      } else if (sortVal === "year") {
        return (b.year || 0) - (a.year || 0);
      }
      return 0;
    });

    papersList.innerHTML = "";

    if (papers.length === 0) {
      papersList.innerHTML = `
        <div class="empty-state" style="text-align: center; padding: 40px; color: var(--text-light); background: #ffffff; border: 1px dashed var(--border-color); border-radius: 8px;">
          <p>No papers match the selected filter criteria.</p>
        </div>
      `;
      return;
    }

    papers.forEach((p) => {
      const card = document.createElement("article");
      const isRetracted = p.retraction && p.retraction.is_retracted;
      card.className = `paper-card ${isRetracted ? "is-retracted-card" : ""}`;

      const titleUrl = p.url || (p.doi ? `https://doi.org/${p.doi}` : "#");

      // Badges
      const studyTypeBadge = `<span class="badge badge-study-type">${escapeHtml(p.study_type || "study")}</span>`;
      const sampleSizeBadge = p.sample_size
        ? `<span class="badge badge-sample-size">${escapeHtml(p.sample_size)}</span>`
        : "";
      const weightBadge = `<span class="badge badge-weight" title="Calculated quality weight">${p.quality_weight || 1.0}x Weight</span>`;
      const citationsBadge = `<span class="badge badge-citations">📊 ${p.citation_count || 0} citations</span>`;
      const yearBadge = `<span class="badge badge-source">📅 ${p.year || "n.d."}</span>`;
      const sourceBadge = `<span class="badge badge-source">🏛️ ${escapeHtml(p.source || "Database")}</span>`;

      // Stance pill
      let stancePillHtml = "";
      if (isRetracted) {
        stancePillHtml = `<span class="stance-pill retracted">⚠️ RETRACTED</span>`;
      } else {
        const stance = (p.stance || "neutral").toLowerCase();
        stancePillHtml = `<span class="stance-pill ${stance}">${stance.toUpperCase()}</span>`;
      }

      // Retraction Alert Box
      let retractionAlertHtml = "";
      if (isRetracted) {
        retractionAlertHtml = `
          <div class="retraction-alert" role="alert">
            <span style="font-size: 18px;">🚫</span>
            <div>
              <strong>RETRACTED PAPER &mdash; EXCLUDED FROM VERDICT</strong>
              <p>${escapeHtml(p.retraction.reason || "This study has been formally retracted.")}</p>
              ${p.retraction.notice_doi ? `<p style="font-size: 12px; margin-top: 4px;">Retraction Notice DOI: <a href="https://doi.org/${escapeHtml(p.retraction.notice_doi)}" target="_blank" rel="noopener noreferrer">${escapeHtml(p.retraction.notice_doi)}</a></p>` : ""}
            </div>
          </div>
        `;
      }

      // Sentence-Level Evidence Callout (Anti-Fabrication Rule)
      let evidenceCalloutHtml = "";
      if (!isRetracted) {
        if (p.quote_verified && p.key_sentence) {
          evidenceCalloutHtml = `
            <div class="evidence-quote-box quote-verified">
              <div class="evidence-quote-header">
                <span>✓ Verified Abstract Quote</span>
              </div>
              <blockquote class="evidence-quote-text">&ldquo;${escapeHtml(p.key_sentence)}&rdquo;</blockquote>
              ${p.reasoning ? `<div class="evidence-reasoning">${escapeHtml(p.reasoning)}</div>` : ""}
            </div>
          `;
        } else {
          evidenceCalloutHtml = `
            <div class="evidence-quote-box quote-unclear">
              <div class="evidence-quote-header">
                <span>Evidence Assessment</span>
              </div>
              <div class="evidence-reasoning">${escapeHtml(p.reasoning || "Neutral or inconclusive findings in abstract.")}</div>
            </div>
          `;
        }
      }

      // Collapsible Abstract
      const abstractHtml = p.abstract
        ? `
          <details class="abstract-details">
            <summary class="abstract-summary-tag">View Full Abstract</summary>
            <p class="abstract-full-text">${escapeHtml(p.abstract)}</p>
          </details>
        `
        : "";

      card.innerHTML = `
        <div class="paper-card-header">
          <h3 class="paper-title">
            <a href="${titleUrl}" target="_blank" rel="noopener noreferrer">
              ${escapeHtml(p.title || "Untitled Paper")}
            </a>
          </h3>
          ${stancePillHtml}
        </div>
        <div class="paper-badges">
          ${studyTypeBadge}
          ${sampleSizeBadge}
          ${weightBadge}
          ${citationsBadge}
          ${yearBadge}
          ${sourceBadge}
        </div>
        ${retractionAlertHtml}
        ${evidenceCalloutHtml}
        ${abstractHtml}
      `;

      papersList.appendChild(card);
    });
  }

  filterStance.addEventListener("change", renderPapersListSafely);
  sortPapers.addEventListener("change", renderPapersListSafely);

  // -------------------------------------------------------------------------
  // Report Exports (Markdown and Print to PDF)
  // -------------------------------------------------------------------------
  try {
    btnExportMd.addEventListener("click", async () => {
      if (!currentResult) return;
      try {
        btnExportMd.disabled = true;
        const res = await fetch("/api/export/markdown", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(currentResult),
        });
        if (!res.ok) throw new Error("Failed to generate Markdown report.");

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `citeverify_report_${Date.now()}.md`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);
      } catch (err) {
        console.error("Error exporting Markdown report:", err);
        alert("Error exporting report: " + err.message);
      } finally {
        btnExportMd.disabled = false;
      }
    });

    btnExportPdf.addEventListener("click", () => {
      window.print();
    });
  } catch (err) {
    console.error("Error setting up report export buttons:", err);
  }

  // -------------------------------------------------------------------------
  // Article Text & URL Claim Extraction Handlers
  // -------------------------------------------------------------------------
  formText.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = articleTextInput.value.trim();
    if (!text) return;
    await extractClaims({ text });
  });

  formUrl.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = articleUrlInput.value.trim();
    if (!url) return;
    await extractClaims({ url });
  });

  async function extractClaims(payload) {
    hideError();
    resultsSection.setAttribute("hidden", "true");
    extractedClaimsSection.setAttribute("hidden", "true");
    showProgress();
    updateProgress(35, "Analyzing article and extracting testable scientific claims...", "");

    try {
      const res = await fetch("/api/extract-claims", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      hideProgress();

      if (!res.ok) {
        showError(data.error || "Extraction Failed", data.message || "Could not extract claims.");
        return;
      }

      renderExtractedClaims(data.extracted_claims || []);
    } catch (err) {
      showError("Extraction Error", "Failed to contact extraction service.");
    }
  }

  function renderExtractedClaims(claims) {
    extractedClaimsList.innerHTML = "";
    if (claims.length === 0) {
      extractedClaimsList.innerHTML = `<p style="padding: 12px; color: var(--text-light);">No testable scientific claims could be identified in this article.</p>`;
    } else {
      claims.forEach((item, index) => {
        const claimText = typeof item === "string" ? item : (item.claim || "");
        const contextText = typeof item === "object" && item.context ? item.context : "";

        const card = document.createElement("div");
        card.className = "claim-item-card";

        const textDiv = document.createElement("div");
        textDiv.innerHTML = `
          <div class="claim-text">${index + 1}. ${escapeHtml(claimText)}</div>
          ${contextText ? `<div class="claim-context">&ldquo;${escapeHtml(contextText)}&rdquo;</div>` : ""}
        `;

        const verifyBtn = document.createElement("button");
        verifyBtn.type = "button";
        verifyBtn.className = "btn btn-secondary";
        verifyBtn.innerHTML = `<span>Verify</span>`;
        verifyBtn.addEventListener("click", () => {
          claimInput.value = claimText;
          const claimTabBtn = document.getElementById("tab-btn-claim");
          if (claimTabBtn) claimTabBtn.click();
          verifyClaimWithProgress(claimText);
        });

        card.appendChild(textDiv);
        card.appendChild(verifyBtn);
        extractedClaimsList.appendChild(card);
      });
    }

    extractedClaimsSection.removeAttribute("hidden");
    extractedClaimsSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  btnCloseClaims.addEventListener("click", () => {
    extractedClaimsSection.setAttribute("hidden", "true");
  });

  // Helper
  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
