"""
MEDthief Web — Streamlit Web-App
Entwickelt für MEDWING GmbH
"""

import streamlit as st
import os, re, tempfile, urllib.parse, time, traceback

try:
    from cv_parser import CVParser, FACHABTEILUNGEN
    from job_searcher import JobSearcher, compute_match_score, _is_relevant
except Exception as e:
    st.error(f"Import-Fehler: {e}\n\n{traceback.format_exc()}")
    st.stop()

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MEDthief — MEDWING Recruiting",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Dark theme overrides */
    .stApp { background-color: #0A0A0B; }

    /* Teal accent buttons */
    .stButton > button {
        background: linear-gradient(180deg, #2DD4BF 0%, #0F766E 100%);
        color: white; border: none; border-radius: 8px;
        font-weight: 700; padding: 0.5rem 1.5rem;
    }
    .stButton > button:hover { background: #2DD4BF; }

    /* Job card styling */
    .job-card {
        background: #17171A; border: 1px solid #27272A;
        border-radius: 12px; padding: 16px; margin-bottom: 12px;
    }
    .job-card:hover { border-color: #2DD4BF; }
    .match-badge {
        display: inline-block; padding: 2px 10px; border-radius: 12px;
        font-size: 12px; font-weight: 700;
    }
    .match-high { background: #065F46; color: #6EE7B7; }
    .match-mid { background: #713F12; color: #FCD34D; }
    .match-low { background: #7F1D1D; color: #FCA5A5; }

    /* Source tag */
    .source-tag {
        display: inline-block; padding: 2px 8px; border-radius: 8px;
        font-size: 11px; background: #1F1F23; color: #D4D4D8;
        margin-right: 6px;
    }

    /* Contact info */
    .contact-pill {
        display: inline-block; padding: 4px 12px; border-radius: 16px;
        font-size: 12px; margin: 2px 4px; background: #131316;
        border: 1px solid #3F3F46; color: #5EEAD4;
    }

    /* Sidebar styling */
    section[data-testid="stSidebar"] {
        background-color: #131316;
    }
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    section[data-testid="stSidebar"] .stSelectbox,
    section[data-testid="stSidebar"] .stTextInput,
    section[data-testid="stSidebar"] .stMultiSelect,
    section[data-testid="stSidebar"] .stSlider {
        margin-bottom: 0.25rem;
    }
    section[data-testid="stSidebar"] label {
        font-size: 12px !important;
        color: #A1A1AA !important;
        margin-bottom: 2px !important;
    }
    section[data-testid="stSidebar"] .stColumns {
        gap: 0.5rem;
    }

    /* Stats */
    .stat-box {
        text-align: center; padding: 12px;
        background: #17171A; border-radius: 8px;
        border: 1px solid #27272A;
    }
    .stat-num { font-size: 24px; font-weight: 800; color: #2DD4BF; }
    .stat-label { font-size: 11px; color: #71717A; }

    /* Hide Streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)


# ── Session State Init ───────────────────────────────────────────────────────
if "cv_parser" not in st.session_state:
    st.session_state.cv_parser = CVParser()
if "job_searcher" not in st.session_state:
    st.session_state.job_searcher = JobSearcher()
if "cv_result" not in st.session_state:
    st.session_state.cv_result = None
if "jobs" not in st.session_state:
    st.session_state.jobs = []
if "candidate_info" not in st.session_state:
    st.session_state.candidate_info = {}
if "job_status" not in st.session_state:
    st.session_state.job_status = {}
if "search_done" not in st.session_state:
    st.session_state.search_done = False


# ── Helper: CV-Felder extrahieren ────────────────────────────────────────────
def _c(v, n=60):
    return (v or "").split("\n")[0].strip()[:n]


def apply_cv(result: dict):
    """CV-Parse-Ergebnis in Session State übernehmen."""
    if result.get("error"):
        st.error(f"Fehler: {result['error']}")
        return

    job_title   = _c(result.get("job_title", ""), 60)
    einrichtung = _c(result.get("facility_type", ""), 40)
    dept        = _c(result.get("fachabteilungen", ""), 50)
    raw_addr    = result.get("wohnort", "") or result.get("location", "")
    wohnort     = re.sub(r",?\s*Deutschland.*", "", raw_addr, flags=re.I).strip()
    wohnort     = _c(wohnort, 80)

    stelle_raw = (result.get("stelle_typ", "") or "").lower()
    if "vollzeit" in stelle_raw and "teilzeit" in stelle_raw:
        arbeitszeit = "Vollzeit / Teilzeit"
    elif "vollzeit" in stelle_raw:
        arbeitszeit = "Vollzeit"
    elif "teilzeit" in stelle_raw:
        arbeitszeit = "Teilzeit"
    else:
        arbeitszeit = ""

    sch_raw = (result.get("schichten", "") or "").lower()
    if "dauernacht" in sch_raw or ("nacht" in sch_raw and "tag" not in sch_raw):
        schicht = "Dauernacht"
    elif "wechsel" in sch_raw:
        schicht = "Wechselschicht"
    elif "tag" in sch_raw and "früh" not in sch_raw and "spät" not in sch_raw:
        schicht = "Tagdienst"
    elif "früh" in sch_raw or "spät" in sch_raw:
        schicht = "Früh- & Spätschicht"
    else:
        schicht = ""

    st.session_state.cv_result = {
        "job_title": job_title,
        "einrichtung": einrichtung,
        "dept": dept,
        "wohnort": wohnort,
        "arbeitszeit": arbeitszeit,
        "schicht": schicht,
        "name": _c(result.get("name", ""), 40),
        "verfuegbar_ab": _c(result.get("verfuegbar_ab", ""), 30),
        "is_medwing": result.get("is_medwing", False),
    }
    st.session_state.candidate_info = {
        "name": _c(result.get("name", ""), 40),
        "job_title": job_title,
        "einrichtung": einrichtung,
        "fachabteilungen": dept,
        "verfuegbar_ab": _c(result.get("verfuegbar_ab", ""), 30),
        "wohnort": wohnort,
        "arbeitszeit": arbeitszeit,
        "schichten": schicht,
    }


# ── Helper: Akquise-Email generieren ─────────────────────────────────────────
def build_email_template(job: dict, candidate: dict) -> tuple:
    """Gibt (betreff, body) zurück."""
    company = job.get("company", "")
    contact = job.get("contact_name", "")
    c_title = candidate.get("job_title", "")
    c_fach  = candidate.get("fachabteilungen", "")
    c_start = candidate.get("verfuegbar_ab", "")
    c_einr  = candidate.get("einrichtung", "")
    c_zeit  = candidate.get("arbeitszeit", "")

    _seniorenheim_keywords = (
        "altenheim", "pflegeheim", "seniorenheim", "altenpflege",
        "seniorenpflege", "seniorenzentrum", "stationäre pflege",
    )
    is_seniorenheim = any(
        kw in (c_einr or "").lower() or kw in (company or "").lower()
        for kw in _seniorenheim_keywords
    )

    greeting = f"Sehr geehrte/r {contact}," if contact else "Sehr geehrte Damen und Herren,"
    subj_title = c_title or "Pflegefachkraft"
    betreff = f"Passende/r {subj_title} für Ihre ausgeschriebene Stelle"

    profil_bullets = []
    if c_title:
        profil_bullets.append(f"Qualifikation: {c_title}")
    if c_fach and not is_seniorenheim:
        fach_parts = [f.strip() for f in re.split(r'[,;]', c_fach) if f.strip()]
        if fach_parts:
            profil_bullets.append("Erfahrung in: " + ", ".join(fach_parts))
    if c_start:
        profil_bullets.append(f"Verfügbar ab: {c_start}")
    if c_zeit:
        profil_bullets.append(f"Arbeitszeit: {c_zeit}")
    profil_block = "\n".join(f"  • {b}" for b in profil_bullets)

    body = (
        f"{greeting}\n\n"
        f"mein Name ist David Böser, ich bin Karriereberater bei MEDWING – "
        f"spezialisiert auf die Direktvermittlung von Pflegefachkräften und "
        f"medizinischem Personal in die Festanstellung. Deutschlandweit "
        f"unterstützen wir Einrichtungen dabei, offene Stellen schnell und "
        f"passgenau zu besetzen.\n\n"
        f"Ich schreibe Ihnen, weil ich aktuell eine/n examinierte/n "
        f"{subj_title} betreue, die/der sich gezielt auf Ihre "
        f"ausgeschriebene Stelle bewirbt und die Anforderungen "
        f"sehr gut erfüllt.\n\n"
    )
    if profil_block:
        body += f"Kurzes Kandidatenprofil:\n{profil_block}\n\n"
    body += (
        f"Was eine Zusammenarbeit mit MEDWING für Sie bedeutet:\n\n"
        f"  ✓ Niedrige Gebühr: Unsere Vermittlungspauschale liegt "
        f"deutlich unter dem Branchenschnitt und häufig auch unter "
        f"Ihren eigenen Recruiting-Kosten.\n\n"
        f"  ✓ Abgesichertes Risiko: Bei Nichtantritt erhalten Sie die "
        f"Gebühr vollständig zurück. Endet das Arbeitsverhältnis während "
        f"der Probezeit, greift unsere gestaffelte Rückvergütung.\n\n"
        f"  ✓ Kein Aufwand: Wir übernehmen Vorqualifizierung, "
        f"Dokumentenprüfung und Koordination. "
        f"Sie führen nur noch das Gespräch.\n\n"
        f"Ich sende Ihnen das anonymisierte Kurzprofil gerne zu. "
        f"Falls Sie zunächst mehr über unsere Arbeitsweise oder die "
        f"Konditionen einer Kooperationsvereinbarung erfahren möchten, "
        f"stehe ich selbstverständlich auch dafür zur Verfügung. "
        f"Eine kurze Rückmeldung genügt, ich melde mich dann "
        f"innerhalb von 24 Stunden.\n\n"
        f"Beste Grüße\n"
        f"David Böser\n"
        f"MEDWING GmbH · Recruiting"
    )
    return betreff, body


def build_anon_pdf(candidate: dict) -> str:
    """Erzeugt anonymisierten Kandidaten-PDF. Gibt Pfad zurück."""
    from fpdf import FPDF
    c = candidate
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_fill_color(15, 118, 110)
    pdf.rect(0, 0, 210, 38, "F")
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(255, 255, 255)
    pdf.set_y(10)
    pdf.cell(0, 10, "MEDWING - Kandidatenprofil (anonymisiert)",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)
    pdf.set_text_color(30, 30, 30)

    def _section(title):
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_fill_color(230, 245, 243)
        pdf.cell(0, 9, f"  {title}", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 11)

    def _row(label, value):
        if not value:
            return
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(55, 7, f"{label}:", new_x="END")
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    _section("Qualifikation")
    _row("Beruf", c.get("job_title", ""))
    _row("Einrichtungsart", c.get("einrichtung", ""))
    if c.get("fachabteilungen"):
        _row("Fachbereiche", c["fachabteilungen"])
    pdf.ln(4)

    _section("Verfügbarkeit")
    _row("Verfügbar ab", c.get("verfuegbar_ab", "") or "auf Anfrage")
    _row("Arbeitszeit", c.get("arbeitszeit", "") or "flexibel")
    if c.get("schichten"):
        _row("Schichtbereitschaft", c["schichten"])
    _row("Region", c.get("wohnort", ""))
    pdf.ln(4)

    _section("Hinweis")
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.multi_cell(0, 6,
        "Dieses Profil wurde zum Schutz der Kandidatendaten anonymisiert. "
        "Persönliche Daten (Name, Adresse, Kontakt) werden erst nach "
        "einer Kooperationsvereinbarung mit MEDWING übermittelt."
    )

    pdf.set_y(-25)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, "MEDWING GmbH | Vertraulich", align="C")

    path = os.path.join(tempfile.gettempdir(), "MEDWING_Kandidatenprofil_anonym.pdf")
    pdf.output(path)
    return path


# ══════════════════════════════════════════════════════════════════════════════
#                              SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🏥 MEDthief")
    st.caption("MEDWING Recruiting Tool")
    st.divider()

    # ── PDF Upload ───────────────────────────────────────────────────────
    st.markdown("### 📄 CV hochladen")
    uploaded_file = st.file_uploader(
        "PDF-Lebenslauf", type=["pdf"], label_visibility="collapsed"
    )

    if uploaded_file and uploaded_file.name != st.session_state.get("_last_pdf"):
        st.session_state._last_pdf = uploaded_file.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        with st.spinner("CV wird analysiert ..."):
            try:
                result = st.session_state.cv_parser.parse(tmp_path)
                apply_cv(result)
            except Exception as e:
                st.error(f"Parse-Fehler: {e}")
        os.unlink(tmp_path)

    st.divider()

    # ── Suchfelder ───────────────────────────────────────────────────────
    st.markdown("### 🔍 Suchparameter")

    cv = st.session_state.cv_result or {}

    job_title = st.text_input("Berufsbezeichnung", value=cv.get("job_title", ""))
    wohnort = st.text_input("Wohnort / PLZ", value=cv.get("wohnort", ""))

    EINRICHTUNGSARTEN = [
        "",
        "Krankenhaus / Klinik",
        "Altenpflege / Pflegeheim",
        "Ambulanter Pflegedienst",
        "Intensivpflegedienst",
        "Psychiatrie",
        "Rehabilitation",
        "Kinderklinik / Pädiatrie",
    ]
    _einr_default = cv.get("einrichtung", "")
    _einr_idx = EINRICHTUNGSARTEN.index(_einr_default) if _einr_default in EINRICHTUNGSARTEN else 0
    einrichtung = st.selectbox("Einrichtungsart", EINRICHTUNGSARTEN, index=_einr_idx)

    # Fachabteilungen als Multiselect
    default_depts = [d.strip() for d in cv.get("dept", "").split(",") if d.strip()]
    valid_defaults = [d for d in default_depts if d in FACHABTEILUNGEN]
    fachabteilungen = st.multiselect(
        "Fachabteilungen",
        options=sorted(FACHABTEILUNGEN),
        default=valid_defaults,
    )

    col1, col2 = st.columns(2)
    with col1:
        arbeitszeit = st.selectbox(
            "Arbeitszeit",
            ["", "Vollzeit", "Teilzeit", "Vollzeit / Teilzeit"],
            index=["", "Vollzeit", "Teilzeit", "Vollzeit / Teilzeit"].index(
                cv.get("arbeitszeit", "")
            ) if cv.get("arbeitszeit", "") in ["", "Vollzeit", "Teilzeit", "Vollzeit / Teilzeit"] else 0,
        )
    with col2:
        _schicht_opts = ["", "Tagdienst", "Wechselschicht", "Dauernacht", "Früh- & Spätschicht"]
        _schicht_default = cv.get("schicht", "")
        schicht = st.selectbox(
            "Schicht",
            _schicht_opts,
            index=_schicht_opts.index(_schicht_default) if _schicht_default in _schicht_opts else 0,
        )

    radius = st.slider("Umkreis (km)", 5, 100, 25, step=5)

    # ── Suchen-Button ────────────────────────────────────────────────────
    # Sync candidate_info mit aktuellen Formularwerten
    if cv:
        st.session_state.candidate_info = {
            "name": cv.get("name", ""),
            "job_title": job_title,
            "einrichtung": einrichtung,
            "fachabteilungen": ", ".join(fachabteilungen) if fachabteilungen else cv.get("dept", ""),
            "verfuegbar_ab": cv.get("verfuegbar_ab", ""),
            "wohnort": wohnort,
            "arbeitszeit": arbeitszeit,
            "schichten": schicht,
        }

    search_clicked = st.button("🔍  Stellen suchen", use_container_width=True, type="primary")

    # CV-Info anzeigen
    if cv:
        st.divider()
        st.markdown("### 👤 Kandidat")
        if cv.get("is_medwing"):
            st.success("MEDWING Kurzprofil erkannt")
        info_lines = []
        for k, v in [("Name", cv.get("name")), ("Beruf", cv.get("job_title")),
                      ("Ort", cv.get("wohnort")), ("Einrichtung", cv.get("einrichtung")),
                      ("Abt.", cv.get("dept")), ("Verfügbar", cv.get("verfuegbar_ab"))]:
            if v:
                info_lines.append(f"**{k}:** {v}")
        st.markdown("  \n".join(info_lines))


# ══════════════════════════════════════════════════════════════════════════════
#                            MAIN CONTENT
# ══════════════════════════════════════════════════════════════════════════════

# ── Suche ausführen ──────────────────────────────────────────────────────────
if search_clicked and not job_title:
    st.warning("Bitte Berufsbezeichnung eingeben.")

if search_clicked and job_title:
    dept_str = ", ".join(fachabteilungen)
    progress_bar = st.progress(0, text="Suche startet ...")

    with st.spinner("Stellen werden gesucht ..."):
        try:
            searcher = st.session_state.job_searcher
            jobs = searcher.search(
                job_title=job_title,
                address=wohnort,
                department=dept_str,
                einrichtung=einrichtung,
                radius=radius,
                arbeitszeit=arbeitszeit,
                schicht=schicht,
            )

            # Post-Filter
            if job_title:
                jobs = [j for j in jobs if _is_relevant(j.get("title", ""), job_title)]

            # Match-Scores berechnen
            for job in jobs:
                job["match_score"] = compute_match_score(
                    job, job_title, einrichtung, radius
                )

            # Sortieren
            _SOURCE_PRIO = {"pflegia": 0}
            jobs = sorted(
                jobs,
                key=lambda j: (
                    0 if j.get("facility_match", True) else 1,
                    _SOURCE_PRIO.get(j.get("source", ""), 5),
                    -(j.get("match_score") or 0),
                    j.get("distance_km") or 9999,
                ),
            )

            st.session_state.jobs = jobs
            st.session_state.search_done = True
            progress_bar.progress(100, text=f"{len(jobs)} Stellen gefunden!")

        except Exception as e:
            st.error(f"Suchfehler: {e}")
            import traceback
            traceback.print_exc()

elif not st.session_state.search_done:
    st.markdown("""
    <div style="text-align: center; padding: 80px 20px; color: #71717A;">
        <h1 style="font-size: 48px; margin-bottom: 8px;">🏥</h1>
        <h2 style="color: #D4D4D8;">MEDthief</h2>
        <p>CV hochladen oder Suchparameter eingeben und <strong>Stellen suchen</strong> klicken.</p>
    </div>
    """, unsafe_allow_html=True)


# ── Ergebnisse anzeigen ──────────────────────────────────────────────────────
jobs = st.session_state.jobs

if jobs:
    # ── Stats ────────────────────────────────────────────────────────────
    sources = {}
    for j in jobs:
        s = j.get("source", "Unbekannt")
        sources[s] = sources.get(s, 0) + 1
    with_contact = sum(1 for j in jobs if j.get("contact_email"))
    avg_dist = [j["distance_km"] for j in jobs if j.get("distance_km")]

    st.markdown("---")
    cols = st.columns(4)
    with cols[0]:
        st.metric("Stellen", len(jobs))
    with cols[1]:
        st.metric("Mit Kontakt", with_contact)
    with cols[2]:
        st.metric("Quellen", len(sources))
    with cols[3]:
        if avg_dist:
            st.metric("Ø Entfernung", f"{sum(avg_dist)/len(avg_dist):.0f} km")
        else:
            st.metric("Ø Entfernung", "–")

    # Source filter
    source_tags = ["Alle"] + sorted(sources.keys())
    selected_source = st.selectbox("Quelle filtern", source_tags, label_visibility="collapsed")

    filtered_jobs = jobs if selected_source == "Alle" else [
        j for j in jobs if j.get("source") == selected_source
    ]

    st.markdown(f"**{len(filtered_jobs)} Ergebnisse** angezeigt")
    st.markdown("---")

    # ── Job Cards ────────────────────────────────────────────────────────
    for i, job in enumerate(filtered_jobs):
        score = job.get("match_score", 0)
        if score >= 70:
            badge_class = "match-high"
        elif score >= 40:
            badge_class = "match-mid"
        else:
            badge_class = "match-low"

        title = job.get("title", "Unbekannt")
        company = job.get("company", "")
        location = job.get("location", "")
        source = job.get("source", "")
        dist = job.get("distance_km")
        url = job.get("url", "#")
        contact_name = job.get("contact_name", "")
        contact_email = job.get("contact_email", "")
        contact_phone = job.get("contact_phone", "")

        dist_str = f" · {dist:.0f} km" if dist else ""

        with st.container():
            st.markdown(f"""
            <div class="job-card">
                <div style="display: flex; justify-content: space-between; align-items: start;">
                    <div>
                        <span class="source-tag">{source}</span>
                        <span class="match-badge {badge_class}">{score}%</span>
                        {f'<span style="color: #71717A; font-size: 12px;">{dist_str}</span>' if dist_str else ''}
                    </div>
                </div>
                <h3 style="color: #FAFAFA; margin: 8px 0 4px 0; font-size: 16px;">
                    <a href="{url}" target="_blank" style="color: #99F6E4; text-decoration: none;">{title}</a>
                </h3>
                <p style="color: #D4D4D8; margin: 0; font-size: 14px;">
                    🏢 {company} {f'· 📍 {location}' if location else ''}
                </p>
            </div>
            """, unsafe_allow_html=True)

            # Expandable Details
            with st.expander(f"Details & Kontakt — {company}", expanded=False):
                det_cols = st.columns([2, 1])

                with det_cols[0]:
                    # Contact info
                    if contact_name or contact_email or contact_phone:
                        st.markdown("**Kontaktdaten:**")
                        if contact_name:
                            st.markdown(f"👤 {contact_name}")
                        if contact_email:
                            st.markdown(f"📧 `{contact_email}`")
                        if contact_phone:
                            st.markdown(f"📞 `{contact_phone}`")
                    else:
                        st.caption("Keine Kontaktdaten gefunden")

                    # Zusatzinfos
                    if job.get("employment_type"):
                        st.markdown(f"📋 {job['employment_type']}")

                with det_cols[1]:
                    # Action Buttons
                    st.link_button("🔗 Stelle öffnen", url, use_container_width=True)

                    # Akquise Email
                    if contact_email and st.session_state.candidate_info:
                        betreff, body = build_email_template(
                            job, st.session_state.candidate_info
                        )
                        gmail_url = (
                            "https://mail.google.com/mail/?view=cm&fs=1"
                            f"&to={urllib.parse.quote(contact_email)}"
                            f"&su={urllib.parse.quote(betreff)}"
                            f"&body={urllib.parse.quote(body)}"
                        )
                        st.link_button("📧 Akquise-Email", gmail_url, use_container_width=True)

                    # Status (stabil per Job-URL, nicht Index)
                    status_key = f"status_{hash(url)}"
                    new_status = st.selectbox(
                        "Status", ["—", "Kontaktiert", "Interesse", "Abgelehnt", "Vermittelt"],
                        key=status_key, label_visibility="collapsed",
                    )

    # ── Anon-PDF Download ────────────────────────────────────────────────
    if st.session_state.candidate_info:
        st.markdown("---")
        st.markdown("### 📄 Anonymisiertes Kandidatenprofil")
        if st.button("PDF generieren & herunterladen"):
            pdf_path = build_anon_pdf(st.session_state.candidate_info)
            with open(pdf_path, "rb") as f:
                st.download_button(
                    "⬇️ PDF herunterladen",
                    f.read(),
                    file_name="MEDWING_Kandidatenprofil_anonym.pdf",
                    mime="application/pdf",
                )
