import json
import os
import sqlite3
import base64
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from textwrap import dedent
from io import BytesIO

import subprocess
subprocess.run(["playwright", "install", "chromium"], check=False)
import anthropic
import streamlit as st
from playwright.sync_api import sync_playwright
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# =====================================================================
# CONFIG
# =====================================================================

DB_PATH = "qa_reports.db"
ALLOWED_EMAIL_DOMAIN = "storable.com"

SEVERITY_COLORS = {
    "P0": "#ff4b4b",
    "P1": "#ff9800",
    "P2": "#ffeb3b",
    "P3": "#9e9e9e",
}

GO_LIVE_COLORS = {
    "YES": "#2ecc71",
    "NO": "#e74c3c",
    "AT RISK": "#f39c12",
}

IMPLEMENTATION_CONSULTANTS = ["Olivia", "Pavel", "Other"]

# =====================================================================
# MASTER PROMPT
# =====================================================================

def build_master_prompt(ic_name: str, company_name: str = "Storable") -> str:
    return dedent(f"""
    INTERNAL STYLE REFERENCE (IMPLEMENTATION NOTES):
    - Fix header overflow
    - Add icons for categories
    - Turn on Trip Protection
    - Boats not available on weekends — intentional?
    - Add descriptions to boats
    Match this tone: short, punchy, action-oriented, slightly informal.

    ---

    ROLE & CONTEXT
    You are the Lead Quality Control Auditor for {company_name} Rentals.
    You are reviewing screenshots of a boat rental booking website captured via automation.
    Business priority: Maximize bookings and add-on revenue.
    - Missing insurance or trip protection = ALWAYS critical.
    - Missing add-ons = ALWAYS critical.
    Be EXTREMELY specific. Never be generic.
    Always use exact names from the screenshots (boats, categories, add-ons).
    Always name exact boats/add-ons/categories impacted.

    Implementation Consultant: {ic_name}
    TONE ADJUSTMENT FOR IMPLEMENTATION NOTES:
    - Olivia: softer, supportive, encouraging, still direct.
    - Pavel: very direct, efficient, minimal words, bullet-only.
    - Default: balanced, clear, pragmatic.

    ---

    SEVERITY SYSTEM (USE THIS FOR EVERY ISSUE):
    P0 = Critical — blocks revenue or booking, missing add-ons/insurance/pricing, major checkout failures
    P1 = High — major UX issue or missing key feature, confusing flows causing drop-off
    P2 = Medium — important but not blocking, could impact trust/conversion
    P3 = Low — nice to have, polish, copy tweaks

    SMART LOGIC (ALWAYS APPLY):
    - NO add-ons detected → at least 1 P0 issue for missing add-ons
    - NO insurance/trip protection → at least 1 P0 issue for missing insurance
    - Missing images across multiple boats → at least 1 P1 issue
    - Only minor UI issues → P2 or P3 only, no P0/P1
    - Insurance/add-ons missing → Go Live Status must be NO

    SCORING LEGEND (human-readable sections only):
    🟢 Excellent | 🟡 Passable | 🔴 Needs Fix | 🚨 Not Done At All

    ---

    OUTPUT STRUCTURE — follow this order exactly:

    ======================================================================
    1. GO LIVE READINESS DECISION
    ======================================================================
    Go Live Status: YES | NO | AT RISK
    Confidence: High | Medium | Low
    Reason: <1-3 short sentences focusing on revenue and booking risk>

    Rules:
    - Insurance/trip protection completely missing → NO
    - Add-ons completely missing → NO
    - Major UX issues or missing key revenue features → AT RISK
    - Only minor issues (P2/P3) and core booking + add-ons + insurance work → YES

    ======================================================================
    2. FULL QC REPORT
    ======================================================================

    ## Section 1: Booking Page (Landing & Category Tabs)
    * Category Icons: [Grade]
      * Specific Issue: List EXACT categories missing icons (or "None")
      * Severity: P0/P1/P2/P3
      * Action Item: Upload icons for [exact category names]
    * Tab Functionality: [Grade]
      * Specific Issue: Name EXACT broken tabs
      * Severity: P0/P1/P2/P3
      * Action Item: Fix tab behavior
    * Boat Cards (Images): [Grade]
      * Specific Issue: Name EXACT boats missing images
      * Severity: P0/P1/P2/P3
      * Action Item: Upload images for [boats]
    * Feature Icons: [Grade]
      * Specific Issue: Name EXACT boats + missing icons
      * Severity: P0/P1/P2/P3
      * Action Item: Add icons
    * Descriptions: [Grade]
      * Specific Issue: Name EXACT boats missing descriptions
      * Severity: P0/P1/P2/P3
      * Action Item: Add descriptions

    ## Section 2: Add-Ons & Checkout
    CRITICAL: If ANY accessories or insurance appear → Add-ons = PRESENT (still score quality)
    * Add-On Presence: [Grade]
      * Specific Issue: Describe missing pieces
      * Severity: P0/P1/P2/P3
      * Action Item: Configure add-ons
    * Insurance / Trip Protection: [Grade]
      * Specific Issue: If missing → "Insurance / Trip Protection: NONE FOUND"
      * Severity: P0/P1/P2/P3
      * Action Item: Turn ON Trip Protection immediately
    * Add-On Quality: [Grade]
      * Specific Issue: Name add-ons missing price or image
      * Severity: P0/P1/P2/P3
      * Action Item: Fix price/image for each

    ## Section 3: UX / Flow Observations
    List 3-10 key UX issues ordered by revenue impact first.
    Each bullet MUST include severity:
    - [P0] <short title> — <1-2 sentence description>

    ## Section 4: System / Configuration Observations
    - [P?] <config issue> — tie back to concrete boats/categories/add-ons

    ## SYSTEM TASK: CATEGORY & ENTITY EXTRACTION
    [FOUND_CATEGORIES]Category 1, Category 2[/FOUND_CATEGORIES]
    [FOUND_BOATS]Boat 1, Boat 2[/FOUND_BOATS]
    [FOUND_ADD_ONS]Add-on 1, Add-on 2[/FOUND_ADD_ONS]

    ======================================================================
    5. IMPLEMENTATION NOTES (INTERNAL)
    ======================================================================
    ## Implementation Notes
    ### Front End / Booking Page
    - <note>
    ### Add-Ons / Revenue
    - <note>
    ### UX / Flow
    - <note>
    ### Configuration
    - <note>
    ### Questions
    - Is [X] intentional?
    ### Nice to Have
    - <note>

    ======================================================================
    6. TASK CHECKLIST
    ======================================================================
    [ ] Fix images for: [boats]
    [ ] Add icons for: [categories]
    [ ] Enable Trip Protection for: [boats/categories]
    [ ] Fix add-on pricing for: [add-ons]
    [ ] Investigate availability rules for: [boats/categories]

    ======================================================================
    7. JIRA TICKETS
    ======================================================================
    For EVERY concrete issue (especially P0/P1):
    Ticket 1:
    Title: <short, specific, action-oriented>
    Severity: P0 | P1 | P2 | P3
    Owner: Frontend | Backend | Config | IC
    Description: <2-4 lines with exact boats/categories/add-ons>
    Acceptance Criteria:
    - <bullet>
    - <bullet>

    ======================================================================
    8. MACHINE-READABLE JSON SUMMARY
    ======================================================================
    At the VERY END output a single valid JSON object wrapped EXACTLY like this:

    [STRUCTURED_OUTPUT_JSON]
    {{
      "go_live": {{
        "status": "YES",
        "confidence": "High",
        "reason": "short text"
      }},
      "issues": [
        {{
          "id": 1,
          "title": "short title",
          "description": "1-3 sentences",
          "severity": "P0",
          "area": "Booking Page",
          "related_boats": ["Boat A"],
          "related_categories": ["Category A"],
          "related_add_ons": ["Tube"]
        }}
      ],
      "jira_tickets": [
        {{
          "title": "ticket title",
          "description": "multi-line description",
          "severity": "P0",
          "owner": "Config",
          "acceptance_criteria": ["criterion 1", "criterion 2"]
        }}
      ],
      "implementation_notes_markdown": "the exact Implementation Notes section in Markdown",
      "task_checklist": [
        "Fix images for: Boat 1, Boat 2",
        "Enable Trip Protection"
      ],
      "found_categories": ["Category 1"],
      "found_boats": ["Boat 1"],
      "found_add_ons": ["Tube"]
    }}
    [/STRUCTURED_OUTPUT_JSON]

    RULES: Valid JSON, double quotes, no comments, no trailing commas.
    NEVER be generic. ALWAYS name specific boats/add-ons/categories.
    ALWAYS prioritize revenue-impacting issues first.
    """)

# =====================================================================
# DATABASE
# =====================================================================

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS qa_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            site_url TEXT NOT NULL,
            user_email TEXT NOT NULL,
            ic_name TEXT NOT NULL,
            go_live_status TEXT,
            go_live_confidence TEXT,
            go_live_reason TEXT,
            workflow_status TEXT DEFAULT 'Pending',
            full_report_markdown TEXT NOT NULL,
            implementation_notes_markdown TEXT NOT NULL,
            checklist_markdown TEXT NOT NULL,
            structured_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qa_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            area TEXT NOT NULL,
            related_boats TEXT,
            related_categories TEXT,
            related_add_ons TEXT,
            FOREIGN KEY (report_id) REFERENCES qa_reports(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS qa_jira_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            owner TEXT NOT NULL,
            acceptance_criteria TEXT NOT NULL,
            FOREIGN KEY (report_id) REFERENCES qa_reports(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

def save_report(site_url, user_email, ic_name, human_md, notes_md, checklist_md, structured) -> int:
    conn = get_db()
    cur = conn.cursor()
    gl = structured.get("go_live", {}) or {}
    cur.execute("""
        INSERT INTO qa_reports (
            created_at, site_url, user_email, ic_name,
            go_live_status, go_live_confidence, go_live_reason,
            full_report_markdown, implementation_notes_markdown,
            checklist_markdown, structured_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.utcnow().isoformat(), site_url, user_email, ic_name,
        gl.get("status",""), gl.get("confidence",""), gl.get("reason",""),
        human_md, notes_md, checklist_md, json.dumps(structured),
    ))
    report_id = cur.lastrowid
    for issue in structured.get("issues", []):
        cur.execute("""
            INSERT INTO qa_issues (report_id, title, description, severity, area,
                related_boats, related_categories, related_add_ons)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            report_id, issue.get("title",""), issue.get("description",""),
            issue.get("severity",""), issue.get("area",""),
            ", ".join(issue.get("related_boats",[]) or []),
            ", ".join(issue.get("related_categories",[]) or []),
            ", ".join(issue.get("related_add_ons",[]) or []),
        ))
    for t in structured.get("jira_tickets", []):
        cur.execute("""
            INSERT INTO qa_jira_tickets (report_id, title, description, severity, owner, acceptance_criteria)
            VALUES (?,?,?,?,?,?)
        """, (
            report_id, t.get("title",""), t.get("description",""),
            t.get("severity",""), t.get("owner",""),
            "\n".join(t.get("acceptance_criteria",[]) or []),
        ))
    conn.commit()
    conn.close()
    return report_id

def load_reports(severity_filter=None, workflow_filter=None):
    conn = get_db()
    cur = conn.cursor()
    query = """
        SELECT r.* FROM qa_reports r
        LEFT JOIN qa_issues i ON r.id = i.report_id
    """
    where, params = [], []
    if severity_filter:
        where.append("i.severity IN ({})".format(",".join("?"*len(severity_filter))))
        params.extend(severity_filter)
    if workflow_filter:
        where.append("r.workflow_status IN ({})".format(",".join("?"*len(workflow_filter))))
        params.extend(workflow_filter)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " GROUP BY r.id ORDER BY r.created_at DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows

def load_jira_for_report(report_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM qa_jira_tickets WHERE report_id=? ORDER BY id", (report_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

# =====================================================================
# CRAWLER
# =====================================================================

def run_crawler(url: str) -> List[str]:
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # Landing page
            p1 = "shot_01_landing.png"
            page.screenshot(path=p1, full_page=True)
            paths.append(p1)

            # Try clicking category tabs
            tabs = page.locator("button, [role='tab'], nav a").all()
            for i, tab in enumerate(tabs[:5]):
                try:
                    if tab.is_visible():
                        tab.click()
                        page.wait_for_timeout(1500)
                        p_tab = f"shot_02_tab_{i}.png"
                        page.screenshot(path=p_tab, full_page=True)
                        paths.append(p_tab)
                except Exception:
                    pass

            # Try booking flow
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)
            for selector in ["text=/Book Now/i", "text=/Reserve/i", "text=/Book/i"]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(3000)
                        break
                except Exception:
                    pass

            p3 = "shot_03_booking.png"
            page.screenshot(path=p3, full_page=True)
            paths.append(p3)

            # Add-ons step
            for selector in ["text=/Add-on/i", "text=/Extra/i", "text=/Insurance/i", "text=/Protection/i", "text=/Continue/i", "text=/Next/i"]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            p4 = "shot_04_addons.png"
            page.screenshot(path=p4, full_page=True)
            paths.append(p4)

            # Checkout step
            for selector in ["text=/Checkout/i", "text=/Continue/i", "text=/Next/i"]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            p5 = "shot_05_checkout.png"
            page.screenshot(path=p5, full_page=True)
            paths.append(p5)

        except Exception as e:
            st.warning(f"Crawler note: {e}")
        finally:
            browser.close()
    return paths

# =====================================================================
# CLAUDE INTEGRATION
# =====================================================================

def call_claude(screenshot_paths: List[str], ic_name: str) -> str:
    api_key = st.secrets.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY",""))
    client = anthropic.Anthropic(api_key=api_key)

    content = []

    # Add screenshots as images
    for path in screenshot_paths:
        if os.path.exists(path):
            with open(path, "rb") as f:
                img_data = base64.standard_b64encode(f.read()).decode("utf-8")
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_data},
            })

    # Add the prompt
    content.append({"type": "text", "text": build_master_prompt(ic_name)})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text

def parse_output(full_text: str):
    start_tag = "[STRUCTURED_OUTPUT_JSON]"
    end_tag = "[/STRUCTURED_OUTPUT_JSON]"
    start = full_text.rfind(start_tag)
    end = full_text.rfind(end_tag)
    if start == -1 or end == -1:
        return full_text, {}
    human = full_text[:start].strip()
    try:
        structured = json.loads(full_text[start+len(start_tag):end].strip())
    except Exception:
        structured = {}
    return human, structured

# =====================================================================
# PDF EXPORT
# =====================================================================

def generate_pdf(report_row) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    styles = getSampleStyleSheet()
    story = []

    # Title
    title_style = ParagraphStyle("title", parent=styles["Title"],
                                  fontSize=22, textColor=HexColor("#1a1a2e"), spaceAfter=6)
    story.append(Paragraph("Storable Rentals – QA Report", title_style))
    story.append(Paragraph(f"Site: {report_row['site_url']}", styles["Normal"]))
    story.append(Paragraph(f"Date: {report_row['created_at'][:19]}", styles["Normal"]))
    story.append(Paragraph(f"IC: {report_row['ic_name']}  |  Analyst: {report_row['user_email']}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    # Go Live badge
    status = (report_row["go_live_status"] or "UNKNOWN").upper()
    color_map = {"YES": "#2ecc71", "NO": "#e74c3c", "AT RISK": "#f39c12"}
    badge_color = HexColor(color_map.get(status, "#7f8c8d"))
    badge_style = ParagraphStyle("badge", fontSize=14, textColor=HexColor("#ffffff"),
                                  backColor=badge_color, borderPadding=6,
                                  spaceAfter=4, alignment=TA_CENTER)
    story.append(Paragraph(f"GO LIVE STATUS: {status}", badge_style))
    if report_row["go_live_reason"]:
        story.append(Paragraph(report_row["go_live_reason"], styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))
    story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#cccccc")))
    story.append(Spacer(1, 0.1*inch))

    # Report body
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13,
                          textColor=HexColor("#1a1a2e"), spaceBefore=12, spaceAfter=4)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, spaceAfter=4, leading=14)

    for line in report_row["full_report_markdown"].splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 0.05*inch))
        elif line.startswith("## "):
            story.append(Paragraph(line[3:], h2))
        elif line.startswith("# "):
            story.append(Paragraph(line[2:], styles["Heading1"]))
        else:
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, body))

    story.append(Spacer(1, 0.3*inch))
    story.append(HRFlowable(width="100%", thickness=1, color=HexColor("#cccccc")))
    story.append(Paragraph(f"Generated by Storable QA Engine · {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
                            ParagraphStyle("footer", fontSize=8, textColor=HexColor("#999999"), alignment=TA_CENTER)))

    doc.build(story)
    return buf.getvalue()

# =====================================================================
# AUTH
# =====================================================================

def require_auth():
    if not getattr(st, "user", None) or not st.user.is_logged_in:
        st.set_page_config(page_title="Storable QA Tool", page_icon="🛥️", layout="centered")
        st.markdown("""
            <div style='text-align:center; padding: 3rem 0;'>
                <h1>🛥️ Storable QA Engine</h1>
                <p style='color:#666; font-size:1.1rem;'>Internal tool for Implementation Consultants</p>
            </div>
        """, unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1,1,1])
        with col2:
            st.button("Sign in with Google", on_click=st.login, args=("google",), use_container_width=True, type="primary")
        st.stop()

    email = getattr(st.user, "email", "") or ""
    if not email.endswith(f"@{ALLOWED_EMAIL_DOMAIN}"):
        st.error(f"Access restricted to @{ALLOWED_EMAIL_DOMAIN} accounts. You signed in as {email}.")
        st.button("Sign out", on_click=st.logout)
        st.stop()

# =====================================================================
# UI HELPERS
# =====================================================================

def go_live_badge(status, confidence):
    status = (status or "UNKNOWN").upper()
    color = GO_LIVE_COLORS.get(status, "#7f8c8d")
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:1rem;margin-bottom:0.5rem;'>"
        f"<span style='background:{color};color:white;padding:0.3rem 1rem;"
        f"border-radius:999px;font-weight:700;font-size:1rem;'>GO LIVE: {status}</span>"
        f"<span style='color:#888;'>Confidence: {confidence or 'N/A'}</span></div>",
        unsafe_allow_html=True,
    )

def severity_badge(sev, title):
    color = SEVERITY_COLORS.get(sev, "#9e9e9e")
    st.markdown(
        f"<span style='background:{color};color:black;padding:0.1rem 0.5rem;"
        f"border-radius:0.3rem;font-weight:700;font-size:0.75rem;'>{sev}</span> {title}",
        unsafe_allow_html=True,
    )

# =====================================================================
# MAIN
# =====================================================================

def main():
    st.set_page_config(
        page_title="Storable QA Engine",
        page_icon="🛥️",
        layout="wide",
    )

    init_db()
    require_auth()

    # Header
    col1, col2 = st.columns([5, 2])
    with col1:
        st.title("🛥️ Storable Rentals – QA Engine")
    with col2:
        name = getattr(st.user, "name", "")
        email = getattr(st.user, "email", "")
        st.markdown(
            f"<div style='text-align:right;padding-top:1rem;'>"
            f"<strong>{name}</strong><br/>"
            f"<span style='color:#888;font-size:0.85rem;'>{email}</span><br/>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.button("Sign out", on_click=st.logout)

    st.divider()

    # Sidebar
    with st.sidebar:
        st.header("▶ Run New QA Audit")
        site_url = st.text_input("Booking site URL", placeholder="https://example.checkfront.com")
        ic_name = st.selectbox("Implementation Consultant", IMPLEMENTATION_CONSULTANTS)
        run_btn = st.button("🚀 Run QA Audit", type="primary", use_container_width=True)

        st.divider()
        st.header("Filter Reports")
        workflow_filter = st.multiselect(
            "Workflow Status", ["Pending", "In Progress", "Fixed"],
            default=["Pending", "In Progress"]
        )
        severity_filter = st.multiselect(
            "Issue Severity", ["P0", "P1", "P2", "P3"],
            default=["P0", "P1"]
        )

    # Run audit
    if run_btn:
        if not site_url:
            st.error("Please enter a URL.")
        else:
            if not site_url.startswith("http"):
                site_url = "https://" + site_url
            with st.status("Running QA audit...", expanded=True) as status:
                st.write("📸 Crawling site and capturing screenshots...")
                shots = run_crawler(site_url)
                st.write(f"✅ Captured {len(shots)} screenshots")
                st.write("🤖 Sending to Claude for analysis...")
                full_text = call_claude(shots, ic_name)
                st.write("✅ Analysis complete")
                st.write("💾 Saving report...")
                human_md, structured = parse_output(full_text)
                if not structured:
                    st.error("Could not parse structured output from Claude. Check the raw output below.")
                    st.text(full_text[:2000])
                else:
                    notes_md = structured.get("implementation_notes_markdown", "")
                    checklist_md = "\n".join(f"[ ] {i}" for i in structured.get("task_checklist", []))
                    report_id = save_report(
                        site_url, email, ic_name,
                        human_md, notes_md, checklist_md, structured
                    )
                    status.update(label=f"✅ Report #{report_id} saved!", state="complete")
                    st.rerun()

    # Reports list
    st.subheader("📋 QA Reports")
    reports = load_reports(severity_filter, workflow_filter)

    if not reports:
        st.info("No reports yet. Run your first QA audit from the sidebar.")
        return

    options = {
        f"#{r['id']} — {r['site_url']} — {r['created_at'][:10]} — {r['go_live_status'] or 'N/A'}": r
        for r in reports
    }
    selected = st.selectbox("Select report", list(options.keys()))
    row = options[selected]

    st.divider()
    go_live_badge(row["go_live_status"], row["go_live_confidence"])
    if row["go_live_reason"]:
        st.caption(row["go_live_reason"])

    # PDF download
    pdf_bytes = generate_pdf(row)
    st.download_button(
        "📄 Download PDF Report",
        data=pdf_bytes,
        file_name=f"qa_report_{row['id']}.pdf",
        mime="application/pdf",
    )

    st.write("")
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Full Report", "📝 Implementation Notes", "🎟️ Jira Tickets", "✅ Checklist"])

    with tab1:
        st.markdown(row["full_report_markdown"])

    with tab2:
        st.markdown(row["implementation_notes_markdown"])

    with tab3:
        tickets = load_jira_for_report(row["id"])
        if not tickets:
            st.info("No Jira tickets found.")
        for t in tickets:
            with st.expander(f"{t['severity']} — {t['title']}"):
                severity_badge(t["severity"], t["title"])
                st.markdown(f"**Owner:** {t['owner']}")
                st.markdown(f"**Description:** {t['description']}")
                st.markdown("**Acceptance Criteria:**")
                for line in t["acceptance_criteria"].splitlines():
                    if line.strip():
                        st.markdown(f"- {line.strip()}")

    with tab4:
        st.markdown(row["checklist_markdown"])


if __name__ == "__main__":
    main()
