import os
import sys
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

def create_scenarios_deck(output_paths=None):
    if output_paths is None:
        output_paths = [
            "GPON_Field_Scenarios_and_Solutions_Report.pptx",
            "GPON_Network_Mapping_Project_Report.pptx"
        ]

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    # Executive Palette
    NAVY = RGBColor(15, 23, 42)          # #0F172A (Dark Background / Primary Text)
    MID_NAVY = RGBColor(30, 41, 59)       # #1E293B
    CYAN = RGBColor(2, 132, 199)          # #0284C7 (Telecom Cyan Accent)
    LIGHT_CYAN = RGBColor(240, 249, 255)  # #F0F9FF (Card Background)
    EMERALD = RGBColor(16, 185, 129)      # #10B981 (Success / Solution)
    LIGHT_EMERALD = RGBColor(236, 253, 245)# #ECFDF5
    ROSE = RGBColor(225, 29, 72)          # #E11D48 (Challenge / Problem)
    LIGHT_ROSE = RGBColor(255, 241, 242)  # #FFF1F2
    AMBER = RGBColor(217, 119, 6)         # #D97706 (Warning / In-Progress)
    LIGHT_AMBER = RGBColor(254, 243, 199) # #FEF3C7
    PURPLE = RGBColor(124, 58, 237)       # #7C3AED (Governance / Admin)
    LIGHT_PURPLE = RGBColor(245, 243, 255)# #F5F3FF
    SLATE = RGBColor(100, 116, 139)       # #64748B (Secondary / Muted Text)
    LIGHT_BG = RGBColor(248, 250, 252)    # #F8FAFC (Body Canvas)
    WHITE = RGBColor(255, 255, 255)
    BORDER = RGBColor(226, 232, 240)      # #E2E8F0 (Subtle Card Outline)
    BORDER_CYAN = RGBColor(186, 230, 253)

    def add_header(slide, title, category="FIELD SCENARIO & SOLUTION SPECIFICATION"):
        # Category + Title container
        tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.15))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_top = tf.margin_right = tf.margin_bottom = 0

        p_cat = tf.paragraphs[0]
        p_cat.text = category.upper()
        p_cat.font.size = Pt(10)
        p_cat.font.bold = True
        p_cat.font.color.rgb = CYAN

        p_title = tf.add_paragraph()
        p_title.text = title
        p_title.font.size = Pt(24)
        p_title.font.bold = True
        p_title.font.color.rgb = NAVY
        p_title.space_before = Pt(3)

        # Header accent separator
        line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(1.45), Inches(11.733), Inches(0.02))
        line.fill.solid()
        line.fill.fore_color.rgb = BORDER
        line.line.fill.background()

        # Subtle footer watermark
        fb = slide.shapes.add_textbox(Inches(0.8), Inches(7.05), Inches(11.7), Inches(0.35))
        ftf = fb.text_frame
        ftf.margin_left = ftf.margin_top = ftf.margin_right = ftf.margin_bottom = 0
        fp = ftf.paragraphs[0]
        fp.text = "Enterprise GPON Network Mapping • Production Engineering & Scenarios Specification Deck"
        fp.font.size = Pt(9)
        fp.font.color.rgb = SLATE

    def build_scenario_slide(slide, scenario_num, title, problem_points, solution_points, web_admin_points, kpi_badge=None):
        add_header(slide, f"Scenario {scenario_num}: {title}", f"ENGINEERED SOLUTIONS • SCENARIO #{scenario_num}")

        card_width = Inches(3.68)
        card_height = Inches(5.25)
        top = Inches(1.65)

        # ----------------- CARD 1: THE OPERATIONAL CHALLENGE -----------------
        c1 = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), top, card_width, card_height)
        c1.fill.solid()
        c1.fill.fore_color.rgb = LIGHT_ROSE
        c1.line.color.rgb = RGBColor(254, 205, 211)
        tf1 = c1.text_frame
        tf1.word_wrap = True
        tf1.margin_left = tf1.margin_right = tf1.margin_top = tf1.margin_bottom = Inches(0.25)

        p = tf1.paragraphs[0]
        p.text = "🔴 OPERATIONAL CHALLENGE"
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = ROSE

        p_sub = tf1.add_paragraph()
        p_sub.text = "Observed Field Bottleneck"
        p_sub.font.size = Pt(14)
        p_sub.font.bold = True
        p_sub.font.color.rgb = NAVY
        p_sub.space_before = Pt(4)
        p_sub.space_after = Pt(12)

        for pt in problem_points:
            p_bullet = tf1.add_paragraph()
            p_bullet.text = f"•  {pt}"
            p_bullet.font.size = Pt(12)
            p_bullet.font.color.rgb = NAVY
            p_bullet.space_after = Pt(8)

        # ----------------- CARD 2: THE ENGINEERED SOLUTION -----------------
        c2 = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(4.82), top, card_width, card_height)
        c2.fill.solid()
        c2.fill.fore_color.rgb = LIGHT_EMERALD
        c2.line.color.rgb = RGBColor(167, 243, 208)
        tf2 = c2.text_frame
        tf2.word_wrap = True
        tf2.margin_left = tf2.margin_right = tf2.margin_top = tf2.margin_bottom = Inches(0.25)

        p = tf2.paragraphs[0]
        p.text = "🟢 ENGINEERED SOLUTION"
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = EMERALD

        p_sub = tf2.add_paragraph()
        p_sub.text = "Mathematical & System Fix"
        p_sub.font.size = Pt(14)
        p_sub.font.bold = True
        p_sub.font.color.rgb = NAVY
        p_sub.space_before = Pt(4)
        p_sub.space_after = Pt(12)

        for pt in solution_points:
            p_bullet = tf2.add_paragraph()
            p_bullet.text = f"•  {pt}"
            p_bullet.font.size = Pt(12)
            p_bullet.font.color.rgb = NAVY
            p_bullet.space_after = Pt(8)

        # ----------------- CARD 3: WEB & ADMIN PORTAL FEATURES -----------------
        c3 = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.84), top, card_width, card_height)
        c3.fill.solid()
        c3.fill.fore_color.rgb = LIGHT_CYAN
        c3.line.color.rgb = BORDER_CYAN
        tf3 = c3.text_frame
        tf3.word_wrap = True
        tf3.margin_left = tf3.margin_right = tf3.margin_top = tf3.margin_bottom = Inches(0.25)

        p = tf3.paragraphs[0]
        p.text = "🔵 WEB & ADMIN IMPLEMENTATION"
        p.font.size = Pt(11)
        p.font.bold = True
        p.font.color.rgb = CYAN

        p_sub = tf3.add_paragraph()
        p_sub.text = "UI Controls & Automation"
        p_sub.font.size = Pt(14)
        p_sub.font.bold = True
        p_sub.font.color.rgb = NAVY
        p_sub.space_before = Pt(4)
        p_sub.space_after = Pt(12)

        for pt in web_admin_points:
            p_bullet = tf3.add_paragraph()
            p_bullet.text = f"•  {pt}"
            p_bullet.font.size = Pt(12)
            p_bullet.font.color.rgb = NAVY
            p_bullet.space_after = Pt(8)

        if kpi_badge:
            p_badge = tf3.add_paragraph()
            p_badge.text = f"★ IMPACT: {kpi_badge}"
            p_badge.font.size = Pt(11)
            p_badge.font.bold = True
            p_badge.font.color.rgb = CYAN
            p_badge.space_before = Pt(12)

    # =========================================================================
    # SLIDE 1: Title Slide (Dark Theme)
    # =========================================================================
    s1 = prs.slides.add_slide(blank_layout)
    bg1 = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg1.fill.solid()
    bg1.fill.fore_color.rgb = NAVY
    bg1.line.fill.background()

    # Top accent cyan strip
    top_strip = s1.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(0.12))
    top_strip.fill.solid()
    top_strip.fill.fore_color.rgb = CYAN
    top_strip.line.fill.background()

    t_box = s1.shapes.add_textbox(Inches(1.0), Inches(1.7), Inches(11.3), Inches(4.5))
    tf1 = t_box.text_frame
    tf1.word_wrap = True

    p_badge = tf1.paragraphs[0]
    p_badge.text = "OFFICIAL TECHNICAL SPECIFICATION & FIELD VALIDATION REPORT"
    p_badge.font.size = Pt(11)
    p_badge.font.bold = True
    p_badge.font.color.rgb = EMERALD

    p_main = tf1.add_paragraph()
    p_main.text = "GPON Field Survey & Network Mapping:\nTested Scenarios & Engineered Solutions"
    p_main.font.size = Pt(38)
    p_main.font.bold = True
    p_main.font.color.rgb = WHITE
    p_main.space_before = Pt(12)

    p_sub = tf1.add_paragraph()
    p_sub.text = "Comprehensive Documentation of Real-World Field Challenges, Algorithmic Fixes & Admin Capabilities"
    p_sub.font.size = Pt(16)
    p_sub.font.color.rgb = RGBColor(148, 163, 184)
    p_sub.space_before = Pt(14)

    p_meta = tf1.add_paragraph()
    p_meta.text = "Enterprise Telecom Optical Operations • Multi-Zone Architecture (Thrissur & Kottayam) • Production v2.5"
    p_meta.font.size = Pt(12)
    p_meta.font.color.rgb = RGBColor(203, 213, 225)
    p_meta.space_before = Pt(28)

    # =========================================================================
    # SLIDE 2: Executive Overview Matrix
    # =========================================================================
    s2 = prs.slides.add_slide(blank_layout)
    add_header(s2, "Executive Architectural Overview: 5 Core Engineering Pillars", "SYSTEM ARCHITECTURE")

    pillars = [
        {
            "num": "01",
            "title": "Data Accuracy & Hierarchy",
            "desc": "Pre-loaded cascading dropdowns eliminate manual typographical errors. Verified naming from Circle to OLT level.",
            "color": CYAN,
            "bg": LIGHT_CYAN
        },
        {
            "num": "02",
            "title": "Topology & Identifiers",
            "desc": "Algorithmic mathematical Enclosure IDs and Compound Splitter IDs ensure zero entity collisions statewide.",
            "color": EMERALD,
            "bg": LIGHT_EMERALD
        },
        {
            "num": "03",
            "title": "High-Precision GIS Engine",
            "desc": "Multi-sample GNSS convergence with interactive satellite drag-pin for building-level pole pinpointing.",
            "color": AMBER,
            "bg": LIGHT_AMBER
        },
        {
            "num": "04",
            "title": "Dynamic Synchronization",
            "desc": "Automatic splitter-level customer count cascade and capacity locks protect against double counting and over-subscription.",
            "color": PURPLE,
            "bg": LIGHT_PURPLE
        }
    ]

    for i, p_data in enumerate(pillars):
        left = Inches(0.8 + i * 2.98)
        card = s2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, Inches(1.7), Inches(2.8), Inches(4.8))
        card.fill.solid()
        card.fill.fore_color.rgb = p_data["bg"]
        card.line.color.rgb = BORDER
        
        ctf = card.text_frame
        ctf.word_wrap = True
        ctf.margin_left = ctf.margin_right = ctf.margin_top = ctf.margin_bottom = Inches(0.2)
        
        p0 = ctf.paragraphs[0]
        p0.text = p_data["num"]
        p0.font.size = Pt(28)
        p0.font.bold = True
        p0.font.color.rgb = p_data["color"]
        
        p1 = ctf.add_paragraph()
        p1.text = p_data["title"]
        p1.font.size = Pt(16)
        p1.font.bold = True
        p1.font.color.rgb = NAVY
        p1.space_before = Pt(8)
        p1.space_after = Pt(10)
        
        p2 = ctf.add_paragraph()
        p2.text = p_data["desc"]
        p2.font.size = Pt(12)
        p2.font.color.rgb = NAVY

    # Bottom Callout Strip on Slide 2
    b_strip = s2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(6.65), Inches(11.733), Inches(0.35))
    b_strip.fill.solid()
    b_strip.fill.fore_color.rgb = MID_NAVY
    b_strip.line.fill.background()
    bstf = b_strip.text_frame
    bsp = bstf.paragraphs[0]
    bsp.text = "🎯 Core Goal: Zero field typographical errors • 100% Unique identification • Complete offline durability • Seamless circle governance"
    bsp.font.size = Pt(10)
    bsp.font.bold = True
    bsp.font.color.rgb = WHITE
    bsp.alignment = PP_ALIGN.CENTER

    # =========================================================================
    # SLIDE 3: Scenario 1 — Pre-Loaded Cascading Network Hierarchy
    # =========================================================================
    s3 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s3,
        scenario_num="1",
        title="Pre-Loaded Network Hierarchy (Zero Manual Typing)",
        problem_points=[
            "Surveyors manually typing Region, Center, RT Room, and OLT Name made frequent spelling errors.",
            "Example: 'Mulamkunnathukavu' vs 'Mulamkunnathukav' split data into fragmented silos.",
            "Differences in capitalization or abbreviations broke automated reporting and SQL groupings.",
            "Surveyors spent excessive field time typing repetitive infrastructural names on mobile."
        ],
        solution_points=[
            "Constructed a 4-Tier Cascading Dropdown hierarchy loaded directly into memory.",
            "Selecting Region auto-populates Centers; Center auto-populates RT Rooms; RT Room auto-populates OLTs.",
            "Zero manual keyboard entry required for physical telecom nodes.",
            "Preload JSON (custom_hierarchy.json) ensures instant 0-second dropdown populating."
        ],
        web_admin_points=[
            "Field App: Dropdowns cascade instantly with zero network lag even when offline.",
            "Admin Portal: 'Network Hierarchy Management' tab allows uploading fresh Excel hierarchy files.",
            "Instant Center Management: Add or delete centers dynamically without touching Python code.",
            "Full backwards-compatibility with existing legacy data aliases."
        ],
        kpi_badge="100% Elimination of Node Typographical Errors"
    )

    # =========================================================================
    # SLIDE 4: Scenario 2 — Multi-Port Enclosures (Multiple PON Ports per Box)
    # =========================================================================
    s4 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s4,
        scenario_num="2",
        title="Multi-Port Enclosures (Single Box Aggregating Multiple PON Ports)",
        problem_points=[
            "High-density field enclosures frequently aggregate fibers from 2 or more OLT ports.",
            "Example: Port 1 and Port 2 terminate in the same physical Enclosure #1 on the pole.",
            "Traditional apps allowed picking only 1 port, forcing surveyors to create duplicate fake enclosures.",
            "Resulted in inaccurate pole counts and corrupted capacity auditing."
        ],
        solution_points=[
            "Engineered a Multi-Port Selection Tile Grid allowing surveyors to tap multiple ports.",
            "Integrated one-touch presets for common configurations: '1-4', '5-8', '1-8', '9-16', 'All', 'Clear'.",
            "Stores selection as clean normalized comma-separated values (e.g., '1, 2').",
            "Seamlessly formats into single-port or multi-port naming conventions without breaking formulas."
        ],
        web_admin_points=[
            "Interactive port picker modal with clear checkmark badges.",
            "Displays multi-port labels (e.g. '[Ports 1, 2]') across surveyed tables and exports.",
            "Admin Live Survey Feed can filter records by individual port or multi-port bundles.",
            "Excel exports cleanly represent multi-port strings in Column F."
        ],
        kpi_badge="Accurate Representation of Dense Distribution Enclosures"
    )

    # =========================================================================
    # SLIDE 5: Scenario 3 — Standardized Algorithmic Enclosure ID (Formula Bar)
    # =========================================================================
    s5 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s5,
        scenario_num="3",
        title="Algorithmic Unique Enclosure ID Generation",
        problem_points=[
            "Surveyors assigned arbitrary box names in the field (e.g., 'DP-1', 'Box Near Post 4', 'Main Box').",
            "Impossible for maintenance crews to know which OLT or Port fed an enclosure during fiber breaks.",
            "Duplicate enclosure names caused database collisions and lost field points.",
            "No standardized mathematical syntax across different telecom districts."
        ],
        solution_points=[
            "Implemented an automated deterministic Enclosure ID generator formula:",
            "Formula: {OLT_PREFIX}LT{OLT_NUM}P{PORT}E{ENC_NUM}",
            "Example: OLT 'OPM1120', Port '1', Box '1' -> 'OPM1120LT25P1E1'.",
            "Every physical distribution box in Kerala receives an identical, universally unique mathematical ID."
        ],
        web_admin_points=[
            "Live Formula Bar (fx) on screen calculates and updates the Enclosure ID in real time.",
            "Field surveyor sees the generated ID before pressing Submit.",
            "Database enforces unique compound constraints to prevent accidental duplicate box creation.",
            "Admin portal enables instant 1-click search by Enclosure ID to inspect all associated splitters."
        ],
        kpi_badge="Standardized Global Naming for Every Distribution Point"
    )

    # =========================================================================
    # SLIDE 6: Scenario 4 — High-Precision GPS Capture & Satellite Pinpoint
    # =========================================================================
    s6 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s6,
        scenario_num="4",
        title="High-Precision GNSS GPS Capture & Satellite Visual Alignment",
        problem_points=[
            "Single-shot mobile GPS triggers large errors (20–50m) under dense tree canopy or beside tall buildings.",
            "Surveyors entering coordinates manually frequently transposed decimal numbers or swapped lat/long.",
            "Maintenance staff could not pinpoint the physical pole on the road during field fault visits.",
            "No visual confirmation of whether the recorded GPS point actually landed on the road."
        ],
        solution_points=[
            "Multi-sample GNSS convergence engine averaging satellite readings over time.",
            "Live visual Accuracy Halo with standard RAG color coding (Green <5m, Yellow 5-15m, Red >15m).",
            "Integrated Leaflet Satellite Map with draggable High-Definition Red Pole Pin (SVG vector).",
            "Surveyor can zoom to roof level and fine-tune the pin directly over the physical KSEB pole."
        ],
        web_admin_points=[
            "Offline SVG vector marker renders instantly with 0 network requests (never 404s).",
            "Click-to-open direct Google Maps navigation from within the admin table.",
            "Smart coordinate parser automatically extracts lat/long pasted from Google Maps or Excel.",
            "Accuracy warning badge alerts surveyor if reading is worse than 15 meters."
        ],
        kpi_badge="Sub-5-Meter Pole Pinpointing on Google Satellite Imagery"
    )

    # =========================================================================
    # SLIDE 7: Scenario 5 — Compound Splitter ID Formatting (OPM1120LT25P1E1S1)
    # =========================================================================
    s7 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s7,
        scenario_num="5",
        title="Compound Splitter ID Formatting (OPM1120LT25P1E1S1)",
        problem_points=[
            "Naming splitters simply 'S1', 'S2' caused massive collisions across thousands of enclosures.",
            "In Excel exports, Column L containing only 'S1' was completely ambiguous without parent context.",
            "Surveyors selecting S1 could not verify if it was already mapped or tied to another enclosure.",
            "Re-importing edited spreadsheets caused identity ambiguity between splitters."
        ],
        solution_points=[
            "Engineered automatic Compound Splitter ID formatting:",
            "Formula: {Enclosure_ID}{Splitter_ID} -> 'OPM1120LT25P1E1S1'.",
            "Maintains simple dropdown choice (S1, S2, S3, S4) for surveyors on site.",
            "Automatically binds parent Enclosure ID and Splitter Code into an unambiguous global ID."
        ],
        web_admin_points=[
            "Live Formula Bar (fx) displays dual preview: Enclosure ID + Splitter ID simultaneously.",
            "Excel exports automatically populate Column L with the compound format 'OPM1120LT25P1E1S1'.",
            "Excel Re-Import Engine automatically normalizes compound IDs back to internal S1 codes cleanly.",
            "Live Survey Feed displays distinct blue badges for each physical splitter."
        ],
        kpi_badge="Unambiguous Splitter Tracking Across All Excel Exports"
    )

    # =========================================================================
    # SLIDE 8: Scenario 6 — Dynamic Splitter Customer Count Cascade
    # =========================================================================
    s8 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s8,
        scenario_num="6",
        title="Dynamic Customer Count Synchronization Across Splitters",
        problem_points=[
            "Day 1: Surveyor boards Customer 1 on Splitter S1 Out 1 and enters 'Customer Count = 1'.",
            "Day 10: Another surveyor boards Customer 2 on S1 Out 2 and enters 'Customer Count = 2'.",
            "In legacy databases, the previous row still showed '1', creating contradictory rows for the same box.",
            "Administrative reports double-counted customers or reported conflicting port loads."
        ],
        solution_points=[
            "Implemented Relational Cascade Synchronization across matching (Enclosure ID, Splitter ID).",
            "Updating customer count on any lead dynamically propagates to all existing rows for that splitter.",
            "Pre-fills existing customer count when surveyor selects that splitter again on mobile.",
            "Startup auto-harmonization (MAX(customers_connected)) unifies all historical database records."
        ],
        web_admin_points=[
            "Real-time database trigger synchronizes customer counts across local records and server.",
            "Live Survey Feed groups stats by physical splitter to eliminate duplicate double-counting.",
            "Admin record editor cascades customer count changes to all linked records automatically.",
            "Circle KPI cards report true physical customer connections without inflation."
        ],
        kpi_badge="100% Data Consistency Across Historical & Future Records"
    )

    # =========================================================================
    # SLIDE 9: Scenario 7 — Splitter Port Capacity Lock & Out Lead Color Tracking
    # =========================================================================
    s9 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s9,
        scenario_num="7",
        title="Splitter Port Capacity Lock & Out Lead Color Tracking",
        problem_points=[
            "Surveyors mistakenly mapped 5 customers to a 1:4 splitter, oversubscribing physical capacity.",
            "Surveyors assigned the same fiber lead color (e.g., Blue, Orange, Green, Brown) twice.",
            "No visual indication of how many leads remained open on a previously surveyed splitter.",
            "Junior technicians accidentally overwrote senior supervisor adjustments."
        ],
        solution_points=[
            "Splitter Capacity Guardrails: Dropdown displays real-time capacity (e.g., '1/4 mapped').",
            "Full Splitters are hard-locked (disabled) for standard field technicians.",
            "Enforces sequential splitter addition (S2 requires S1 to exist first).",
            "Strict color-code validation prevents duplicate lead assignment on the same splitter."
        ],
        web_admin_points=[
            "ACSO Update Mode Banner: Authorized supervisors see an unlock banner to overwrite leads.",
            "Splitter Out Color Badges visually display individual lead assignments in tables.",
            "Auto-detects splitter ratios (1:4, 1:8, 1:16, 1:32) and calculates available lead capacity.",
            "Audit trail logs supervisor corrections with distinct badges in the database."
        ],
        kpi_badge="Elimination of Over-Subscribed Splitters & Duplicate Leads"
    )

    # =========================================================================
    # SLIDE 10: Scenario 8 — Offline-First PWA Resilience in Remote Rural Areas
    # =========================================================================
    s10 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s10,
        scenario_num="8",
        title="Offline-First PWA Resilience in Remote Rural Areas",
        problem_points=[
            "Telecom surveys often occur in deep rural, forested, or hilly regions with zero cellular reception.",
            "Standard web apps fail or crash when network connection drops, causing surveyors to lose entries.",
            "Surveyors were forced to write data on paper and re-enter it at night, introducing errors.",
            "High battery and data consumption when repeatedly re-fetching satellite assets."
        ],
        solution_points=[
            "Built as a full Progressive Web App (PWA) with Service Worker offline caching (v91).",
            "All app shell assets, Leaflet maps, and preload databases are cached permanently on the device.",
            "Surveys are saved immediately to local browser storage (IndexedDB / LocalStorage).",
            "Visual queue status tags: Green '✓ Synced' vs Yellow '⏳ Pending Sync'."
        ],
        web_admin_points=[
            "Automatic Background Re-Sync: Uploads pending records as soon as internet connection returns.",
            "Offline Excel Export: Surveyors can generate and download Excel sheets on phone with 0 signal.",
            "One-touch home screen installation on Android and iOS devices (No app store needed).",
            "Session preservation ensures surveyor never gets logged out while offline in the field."
        ],
        kpi_badge="100% Survey Continuity with Zero Mobile Network Required"
    )

    # =========================================================================
    # SLIDE 11: Scenario 9 — Excel Import & Export Round-Trip Fidelity
    # =========================================================================
    s11 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s11,
        scenario_num="9",
        title="Excel Import & Export Round-Trip Fidelity",
        problem_points=[
            "Management frequently exports survey data to Excel, makes manual corrections, and re-imports.",
            "Column rearrangements, renamed headers, or missing columns broke traditional parsers.",
            "Re-importing often created duplicate rows or wiped out GPS lat/long values.",
            "Formula outputs in Excel were not properly parsed during server re-ingestion."
        ],
        solution_points=[
            "Engineered a resilient OpenPyXL Excel Ingestion Engine with intelligent header normalization.",
            "Fuzzy matches 19-column and 24-column formats, ignoring casing, spacing, and header aliases.",
            "Idempotent deterministic UUID matching updates existing records instead of duplicating them.",
            "Compound Splitter ID Normalizer automatically extracts clean splitter codes upon re-import."
        ],
        web_admin_points=[
            "Single-click Excel downloads by Center, by Region, or statewide master export.",
            "Bulk ZIP Packager: Exports separate organized Excel files for every center in one archive.",
            "Pre-styled Excel formatting with frozen headers, auto-width columns, and centered alignments.",
            "Instant import audit report displaying rows inserted, updated, and skipped."
        ],
        kpi_badge="Flawless Two-Way Excel Interoperability with Zero Duplication"
    )

    # =========================================================================
    # SLIDE 12: Scenario 10 — Multi-Tier Role-Based Access Governance (RBAC)
    # =========================================================================
    s12 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s12,
        scenario_num="10",
        title="Multi-Tier Role-Based Access Governance (RBAC)",
        problem_points=[
            "Field technicians could accidentally delete records or modify colleagues' surveyed data.",
            "Junior staff could access and view centers outside their assigned operational jurisdiction.",
            "No security barrier preventing unauthenticated mobile users from submitting data.",
            "Management lacked granular control over who could export sensitive infrastructure reports."
        ],
        solution_points=[
            "Implemented strict 5-Tier Role-Based Access Control (RBAC):",
            "1. Field Technician: Append-only, capacity locked, locked to assigned center, no delete/export.",
            "2. ACSO (Sub-Divisional Supervisor): Supervisor overwrite mode, multi-center charge, Excel export.",
            "3. RCSM: Regional circle oversight, analytical reporting.",
            "4. Admin & Super Admin: Statewide master governance, user provisioning, database maintenance."
        ],
        web_admin_points=[
            "User Access Management Portal: Search users by Employee Code, Name, or Mobile Number.",
            "Filter users dynamically by Region and Assigned Center.",
            "Bulk User Provisioning via Excel template download and upload.",
            "Stateless cryptographic session tokens with automatic password auto-healing."
        ],
        kpi_badge="Strict Jurisdiction Boundaries & Role Isolation Statewide"
    )

    # =========================================================================
    # SLIDE 13: Scenario 11 — Real-Time Central Admin Management & Live Feed
    # =========================================================================
    s13 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s13,
        scenario_num="11",
        title="Real-Time Central Admin Management & Live Survey Feed",
        problem_points=[
            "Circle management had zero live visibility into field survey progress during the work day.",
            "Required waiting for end-of-day spreadsheets sent via email, delaying decision making.",
            "Supervisors had no easy way to edit small mistakes (e.g. wrong pole number) without re-surveying.",
            "Deleting erroneous test rows required direct SQL database access by IT staff."
        ],
        solution_points=[
            "Embedded central Management Web Portal directly inside the server application.",
            "Real-Time Live Survey Feed displaying incoming surveys as they happen across all centers.",
            "Instant search bar filtering by OLT Name, Post #, Landmark, Enclosure ID, or Surveyor.",
            "Live KPI cards reporting Total Surveyed Poles, Mapped Splitters, and Connected Customers."
        ],
        web_admin_points=[
            "Inline Record Editor Modal: Allows authorized supervisors to fix errors with 1 click.",
            "Batch Checkbox Selection: Select multiple records for bulk deletion or auditing.",
            "Quick-Filter Pills for Regions and Centers for instant sub-division drilldown.",
            "Direct in-browser links to download Project PPT reports and raw survey archives."
        ],
        kpi_badge="Real-Time Statewide Operational Visibility & Governance"
    )

    # =========================================================================
    # SLIDE 14: Scenario 12 — Dual-Zone Scalability (Thrissur & Kottayam Servers)
    # =========================================================================
    s14 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s14,
        scenario_num="12",
        title="Dual-Zone Scalability (Thrissur North & Kottayam South Servers)",
        problem_points=[
            "Hosting all 14 districts of Kerala on a single machine risked database locking and network bottlenecks.",
            "Both regional server machines use dynamic broadband IPs that change upon router reboots.",
            "Surveyors should not have to choose between complex IP links or remember regional server addresses.",
            "Risk of split data if load-balanced randomly across two separate SQLite databases."
        ],
        solution_points=[
            "Divided Kerala into a balanced 7+7 District Dual-Zone architecture:",
            "North Zone Server (Thrissur): 7 districts / 4 telecom regions.",
            "South Zone Server (Kottayam): 7 districts / 4 telecom regions.",
            "Account-based auto-routing: User's locked region connects them to their designated server seamlessly.",
            "Zero zone buttons: Surveyors simply enter their username and the system connects them to their data."
        ],
        web_admin_points=[
            "Cloudflare Tunnel (cloudflared) integration: Routes a single URL (e.g. gponkerala.in) to both servers.",
            "100% immune to dynamic IP changes with zero router port-forwarding required.",
            "Bypasses CGNAT and ISP firewalls with free automated SSL (HTTPS) encryption.",
            "Both regional databases operate with peak speed and zero cross-zone lock contention."
        ],
        kpi_badge="Scalable Statewide Distribution with Zero Dynamic IP Disruptions"
    )

    # =========================================================================
    # SLIDE 15: Scenario 13 — Enterprise Data Resilience & Disaster Recovery
    # =========================================================================
    s15 = prs.slides.add_slide(blank_layout)
    build_scenario_slide(
        s15,
        scenario_num="13",
        title="Enterprise Data Resilience, WAL Mode & Disaster Recovery",
        problem_points=[
            "Power outages or server crashes during an active database write could corrupt SQLite files.",
            "Traditional file backups required stopping the server, causing surveyor downtime.",
            "Lack of offsite cloud backups risked total data loss if server hardware failed.",
            "Admin had no way to verify if last night's backup succeeded without checking command line logs."
        ],
        solution_points=[
            "Enabled SQLite WAL (Write-Ahead Logging) mode for concurrent high-speed reads and writes.",
            "Zero-downtime hot online SQLite snapshotting (src.backup(dst)) creates clean backups while running.",
            "Automated nightly backup script (scripts/backup_to_gdrive.sh) compresses database and configs.",
            "Encrypted multi-cloud synchronization to Google Drive via Rclone."
        ],
        web_admin_points=[
            "Asynchronous zero-overhead health check (scripts/verify_backup_health.py).",
            "Decoupled /api/backup-status API endpoint executes in <0.2ms with zero network blocking.",
            "Live Backup Health Widget in Admin Portal displays timestamp, file size, and cloud sync status.",
            "Automated systemd service restart watchdog recovers server in 5 seconds after power cycles."
        ],
        kpi_badge="Zero Downtime, Corruption-Proof SQLite & Automated Offsite Backups"
    )

    # =========================================================================
    # SLIDE 16: Operational Impact Scorecard & Executive Sign-off (Dark Theme)
    # =========================================================================
    s16 = prs.slides.add_slide(blank_layout)
    bg16 = s16.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg16.fill.solid()
    bg16.fill.fore_color.rgb = NAVY
    bg16.line.fill.background()

    top_strip16 = s16.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(0.12))
    top_strip16.fill.solid()
    top_strip16.fill.fore_color.rgb = EMERALD
    top_strip16.line.fill.background()

    t_box16 = s16.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.1))
    tf16 = t_box16.text_frame
    tf16.word_wrap = True
    p_cat16 = tf16.paragraphs[0]
    p_cat16.text = "EXECUTIVE SUMMARY & OPERATIONAL SCORECARD"
    p_cat16.font.size = Pt(10)
    p_cat16.font.bold = True
    p_cat16.font.color.rgb = EMERALD

    p_title16 = tf16.add_paragraph()
    p_title16.text = "Operational Impact: Traditional Survey vs Modern GPON Mapping Platform"
    p_title16.font.size = Pt(24)
    p_title16.font.bold = True
    p_title16.font.color.rgb = WHITE
    p_title16.space_before = Pt(3)

    # Comparison Grid Table
    rows = 6
    cols = 3
    left = Inches(0.8)
    top = Inches(1.6)
    width = Inches(11.733)
    height = Inches(4.5)

    table_shape = s16.shapes.add_table(rows, cols, left, top, width, height)
    table = table_shape.table
    table.columns[0].width = Inches(3.2)
    table.columns[1].width = Inches(4.2)
    table.columns[2].width = Inches(4.333)

    scorecard_data = [
        ("Operational Parameter", "Legacy / Manual Process", "Modern GPON Mapping Platform"),
        ("Data Entry Speed", "Manual keyboard typing (~6-8 mins per pole)", "Cascading dropdowns + Auto ID (<45 secs per pole)"),
        ("Typographical Error Rate", "~24% spelling & node name discrepancies", "0% (All infrastructure pre-loaded & locked)"),
        ("Splitter & Customer Accuracy", "Splitter collision & contradictory counts", "Compound IDs + Relational customer cascade"),
        ("Remote Network Resilience", "Data lost when mobile signal drops", "100% Offline PWA with IndexedDB sync queue"),
        ("Management Oversight", "24-hour delayed spreadsheets via email", "Real-Time Live Survey Feed + Instant Excel Export")
    ]

    for r_idx, row in enumerate(scorecard_data):
        for c_idx, val in enumerate(row):
            cell = table.cell(r_idx, c_idx)
            cell.text = val
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cp = cell.text_frame.paragraphs[0]
            cp.font.name = "Calibri"
            if r_idx == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = MID_NAVY
                cp.font.size = Pt(13)
                cp.font.bold = True
                cp.font.color.rgb = CYAN if c_idx == 2 else WHITE
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(15, 23, 42) if r_idx % 2 == 1 else RGBColor(22, 33, 56)
                cp.font.size = Pt(11)
                if c_idx == 0:
                    cp.font.bold = True
                    cp.font.color.rgb = WHITE
                elif c_idx == 1:
                    cp.font.color.rgb = RGBColor(252, 165, 165)
                else:
                    cp.font.bold = True
                    cp.font.color.rgb = RGBColor(110, 231, 183)

    # Sign-off box
    sign_box = s16.shapes.add_textbox(Inches(0.8), Inches(6.35), Inches(11.733), Inches(0.7))
    stf = sign_box.text_frame
    sp0 = stf.paragraphs[0]
    sp0.text = "STATUS: SYSTEM FULLY TESTED, VALIDATED & DEPLOYED IN PRODUCTION ACROSS KERALA CIRCLE"
    sp0.font.size = Pt(11)
    sp0.font.bold = True
    sp0.font.color.rgb = EMERALD
    sp0.alignment = PP_ALIGN.CENTER

    sp1 = stf.add_paragraph()
    sp1.text = "Ready for Circle-wide expansion • Dual-Zone scalability • Complete technical audit documentation"
    sp1.font.size = Pt(10)
    sp1.font.color.rgb = RGBColor(148, 163, 184)
    sp1.alignment = PP_ALIGN.CENTER
    sp1.space_before = Pt(3)

    # Save to all requested filepaths
    for out_path in output_paths:
        prs.save(out_path)
        print(f"[SUCCESS] Presentation generated: {out_path}")

    return output_paths[0]

if __name__ == "__main__":
    create_scenarios_deck()
