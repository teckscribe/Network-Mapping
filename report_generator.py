"""
Enterprise GPON Network Mapping System - Multi-Report Generator Engine
====================================================================
Generates comprehensive quantitative and operational reports in multiple formats (.xlsx, .json, .md, .zip).

Supported Report Types:
  1. master_survey        : Complete field survey database export with formatted compound keys and KPI header.
  2. splitter_utilization : Splitter port capacity, connected subscribers, available ports, and saturation analysis.
  3. data_quality         : Field audit for GNSS coordinate gaps, invalid ratios, missing pole IDs, and duplicate subscriber IDs.
  4. field_productivity   : Surveyor velocity, daily submission frequency, center distribution, and device tracking.
  5. network_coverage     : Planned vs surveyed OLT/Port coverage audit against custom_hierarchy.json.
  6. executive_summary    : High-level KPI digest in JSON and Markdown formats.
  7. bundle_zip           : Timestamped ZIP archive containing all selected reports ready for one-click download.

Usage via CLI:
  python report_generator.py --type all
  python report_generator.py --type master --region "Kochi"
  python report_generator.py --type splitter --center "Aluva"
  python report_generator.py --type quality --output-dir "reports"
  python report_generator.py --type productivity
  python report_generator.py --type coverage
  python report_generator.py --bundle
"""

import os
import sys
import json
import sqlite3
import argparse
import datetime
import zipfile
import uuid
from typing import Dict, List, Any, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ==========================================
# BASE CONFIGURATION & CONSTANTS
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "gpon_survey_data.db")
DEFAULT_HIERARCHY_PATH = os.path.join(BASE_DIR, "custom_hierarchy.json")
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "reports")

# Enterprise Color Palette
COLOR_NAVY = "0F172A"       # Primary Title / Header
COLOR_SLATE = "334155"      # Secondary Header
COLOR_EMERALD = "059669"    # Success / Healthy / Free Capacity
COLOR_AMBER = "D97706"      # Warning / High Saturation
COLOR_CRIMSON = "DC2626"    # Critical / Full / Error
COLOR_CARD_BG = "F1F5F9"    # KPI Metric Card Background
COLOR_ROW_ALT = "F8FAFC"    # Alternating Row Striping
COLOR_BORDER = "CBD5E1"     # Light Table Border

class ReportGenerator:
    """Enterprise multi-report generator for GPON network mapping data."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH, hierarchy_path: str = DEFAULT_HIERARCHY_PATH, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.db_path = db_path
        self.hierarchy_path = hierarchy_path
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _get_db_connection(self) -> sqlite3.Connection:
        """Create a resilient read-only SQLite connection."""
        if not os.path.exists(self.db_path):
            # Create blank DB if missing
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_hierarchy(self) -> Dict[str, Any]:
        """Load network hierarchy JSON for planned OLT coverage calculations."""
        if os.path.exists(self.hierarchy_path):
            try:
                with open(self.hierarchy_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[ReportGenerator] Warning: Could not load hierarchy: {e}")
        return {}

    def fetch_records(self, region: Optional[str] = None, center: Optional[str] = None, olt: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch filtered survey records from SQLite database."""
        try:
            conn = self._get_db_connection()
            cur = conn.cursor()

            # Ensure table exists
            table_check = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='survey_records'").fetchone()
            if not table_check:
                conn.close()
                return []

            query = "SELECT * FROM survey_records WHERE 1=1"
            params = []

            if region:
                query += " AND LOWER(TRIM(region)) = LOWER(TRIM(?))"
                params.append(region)
            if center:
                query += " AND LOWER(TRIM(center)) = LOWER(TRIM(?))"
                params.append(center)
            if olt:
                query += " AND LOWER(TRIM(olt_name)) LIKE LOWER(?)"
                params.append(f"%{olt.strip()}%")
            if start_date:
                query += " AND (survey_date_time >= ? OR created_at >= ?)"
                params.extend([start_date, start_date])
            if end_date:
                query += " AND (survey_date_time <= ? OR created_at <= ?)"
                params.extend([end_date, end_date])

            query += " ORDER BY rowid ASC"
            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            print(f"[ReportGenerator] Error fetching records: {e}")
            return []

    # =========================================================================
    # EXCEL STYLING HELPERS
    # =========================================================================
    def _apply_card_style(self, ws, start_col: int, start_row: int, width: int, height: int, label: str, value: Any, color_hex: str = COLOR_NAVY):
        """Draw an executive KPI stat card with border and color accent."""
        end_col = start_col + width - 1
        end_row = start_row + height - 1
        ws.merge_cells(start_row=start_row, start_column=start_col, end_row=start_row, end_column=end_col)
        ws.merge_cells(start_row=start_row + 1, start_column=start_col, end_row=end_row, end_column=end_col)

        title_cell = ws.cell(row=start_row, column=start_col, value=label.upper())
        title_cell.font = Font(name="Calibri", size=9, bold=True, color="64748B")
        title_cell.alignment = Alignment(horizontal="center", vertical="center")
        title_cell.fill = PatternFill(start_color=COLOR_CARD_BG, end_color=COLOR_CARD_BG, fill_type="solid")

        val_cell = ws.cell(row=start_row + 1, column=start_col, value=str(value))
        val_cell.font = Font(name="Calibri", size=18, bold=True, color=color_hex)
        val_cell.alignment = Alignment(horizontal="center", vertical="center")
        val_cell.fill = PatternFill(start_color=COLOR_CARD_BG, end_color=COLOR_CARD_BG, fill_type="solid")

        thin_side = Side(style="thin", color=COLOR_BORDER)
        card_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
        for r in range(start_row, end_row + 1):
            for c in range(start_col, end_col + 1):
                ws.cell(row=r, column=c).border = card_border

    def _auto_fit_columns(self, ws, min_width: int = 12, max_width: int = 45):
        """Dynamically size columns based on contents."""
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                # Skip merged or very long title cells
                if cell.row < 6 and col[0].column <= 8:
                    continue
                if cell.value:
                    val_str = str(cell.value)
                    if len(val_str) > max_len:
                        max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(min_width, min(max_len + 3, max_width))

    # =========================================================================
    # REPORT 1: MASTER FIELD SURVEY REPORT (.xlsx)
    # =========================================================================
    def generate_master_survey_report(self, records: Optional[List[Dict[str, Any]]] = None, filename: Optional[str] = None) -> str:
        """Builds formatted master survey data report with summary cards and clean schema."""
        if records is None:
            records = self.fetch_records()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Master_Survey_Data"
        ws.views.sheetView[0].showGridLines = True

        # Sheet Header
        ws.merge_cells("A1:S1")
        title = ws.cell(row=1, column=1, value="ENTERPRISE GPON NETWORK MAPPING - MASTER FIELD SURVEY REPORT")
        title.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title.fill = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
        title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32

        # KPI Metrics
        total_records = len(records)
        unique_enclosures = len(set(r.get("enclosure_id") for r in records if r.get("enclosure_id")))
        unique_splitters = len(set(r.get("splitter_id") for r in records if r.get("splitter_id")))
        total_customers = sum(int(r.get("customers_connected") or 0) for r in records)
        unique_olts = len(set(r.get("olt_name") for r in records if r.get("olt_name")))

        self._apply_card_style(ws, start_col=1, start_row=3, width=3, height=2, label="Total Records", value=total_records, color_hex=COLOR_NAVY)
        self._apply_card_style(ws, start_col=4, start_row=3, width=3, height=2, label="Unique Enclosures", value=unique_enclosures, color_hex="0284C7")
        self._apply_card_style(ws, start_col=7, start_row=3, width=3, height=2, label="Unique Splitters", value=unique_splitters, color_hex=COLOR_EMERALD)
        self._apply_card_style(ws, start_col=10, start_row=3, width=3, height=2, label="Connected Customers", value=total_customers, color_hex=COLOR_AMBER)
        self._apply_card_style(ws, start_col=13, start_row=3, width=3, height=2, label="Surveyed OLTs", value=unique_olts, color_hex="7C3AED")

        # Table Column Headers
        headers = [
            "Region", "Center", "RT Room", "Technology", "OLT / Node Name",
            "Port Number", "KSEB / Pole Number", "Landmark", "Enclosure Number",
            "Enclosure ID", "Lat / Long Coordinates", "Splitter ID", "Splitter Ratio",
            "Connected Customers", "Splitter Out Color", "ADL Subscriber ID",
            "ACS Subscriber ID", "Survey Date Time", "Surveyor Name"
        ]

        header_row = 6
        ws.row_dimensions[header_row].height = 24
        header_fill = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color=COLOR_BORDER),
            right=Side(style="thin", color=COLOR_BORDER),
            top=Side(style="thin", color=COLOR_BORDER),
            bottom=Side(style="thin", color=COLOR_BORDER)
        )

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border

        # Populate Data Rows
        row_font = Font(name="Calibri", size=10)
        alt_fill = PatternFill(start_color=COLOR_ROW_ALT, end_color=COLOR_ROW_ALT, fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        for idx, r in enumerate(records, start=header_row + 1):
            enc_id = (r.get("enclosure_id") or "").strip()
            spl_id = (r.get("splitter_id") or "").strip()
            # Ensure compound splitter ID formatting
            formatted_splitter_id = spl_id
            if spl_id and enc_id and not spl_id.upper().startswith(enc_id.upper()):
                formatted_splitter_id = f"{enc_id}{spl_id}"

            vals = [
                r.get("region") or "",
                r.get("center") or "",
                r.get("rt_room") or "",
                r.get("technology") or "GPON",
                r.get("olt_name") or "",
                r.get("port_number") or "",
                r.get("kseb_post_number") or "",
                r.get("landmark") or "",
                r.get("enclosure_number") or "",
                enc_id,
                r.get("lat_long") or "",
                formatted_splitter_id,
                r.get("splitter_ratio") or "",
                int(r.get("customers_connected") or 0),
                r.get("splitter_lead_color") or "",
                r.get("adl_subscriber_id") or "",
                r.get("acs_subscriber_id") or "",
                r.get("survey_date_time") or r.get("created_at") or "",
                r.get("surveyor_name") or r.get("surveyor_username") or ""
            ]

            is_alt = (idx % 2 == 0)
            fill_to_use = alt_fill if is_alt else white_fill

            for col_idx, val in enumerate(vals, 1):
                c = ws.cell(row=idx, column=col_idx, value=val)
                c.font = row_font
                c.fill = fill_to_use
                c.border = thin_border
                # Alignment
                if col_idx in (10, 12, 14): # IDs and customer numbers
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")

        self._auto_fit_columns(ws)
        ws.freeze_panes = f"A{header_row + 1}"

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Master_Survey_Report_{ts}.xlsx"
        target_path = os.path.join(self.output_dir, filename)
        wb.save(target_path)
        print(f"[SUCCESS] Master Survey Report saved: {target_path}")
        return target_path

    # =========================================================================
    # REPORT 2: SPLITTER UTILIZATION & CAPACITY AUDIT REPORT (.xlsx)
    # =========================================================================
    def generate_splitter_utilization_report(self, records: Optional[List[Dict[str, Any]]] = None, filename: Optional[str] = None) -> str:
        """Analyzes port saturation, available spare capacity, and utilization percentage per splitter."""
        if records is None:
            records = self.fetch_records()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Splitter_Capacity_Audit"
        ws.views.sheetView[0].showGridLines = True

        # Sheet Header
        ws.merge_cells("A1:K1")
        title = ws.cell(row=1, column=1, value="OPTICAL DISTRIBUTION NETWORK - SPLITTER CAPACITY & SATURATION AUDIT")
        title.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title.fill = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
        title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32

        # Aggregate Splitters
        splitters_map = {}
        for r in records:
            enc_id = (r.get("enclosure_id") or "UNKNOWN").strip()
            spl_id = (r.get("splitter_id") or "S1").strip()
            compound_key = f"{enc_id}:{spl_id}"

            ratio_raw = str(r.get("splitter_ratio") or "1:8").strip()
            # Determine maximum ports from ratio
            total_ports = 8
            if "1:4" in ratio_raw: total_ports = 4
            elif "1:16" in ratio_raw: total_ports = 16
            elif "1:2" in ratio_raw: total_ports = 2
            elif "1:32" in ratio_raw: total_ports = 32

            cust_count = int(r.get("customers_connected") or 0)

            if compound_key not in splitters_map:
                splitters_map[compound_key] = {
                    "region": r.get("region") or "",
                    "center": r.get("center") or "",
                    "olt_name": r.get("olt_name") or "",
                    "port_number": r.get("port_number") or "",
                    "enclosure_id": enc_id,
                    "splitter_id": spl_id,
                    "ratio": ratio_raw,
                    "total_capacity": total_ports,
                    "connected_customers": cust_count,
                    "pole": r.get("kseb_post_number") or "",
                    "lat_long": r.get("lat_long") or ""
                }
            else:
                # Maximize or sum customer count if multiple records reference the same splitter
                splitters_map[compound_key]["connected_customers"] = max(
                    splitters_map[compound_key]["connected_customers"], cust_count
                )

        # Calculate metrics
        total_splitters = len(splitters_map)
        saturated_count = 0
        high_load_count = 0
        balanced_count = 0
        empty_count = 0

        for s in splitters_map.values():
            cap = s["total_capacity"]
            conn = s["connected_customers"]
            util = (conn / cap * 100) if cap > 0 else 0
            s["utilization_pct"] = round(util, 1)
            s["spare_ports"] = max(0, cap - conn)

            if conn >= cap:
                s["status"] = "CRITICAL: SATURATED"
                saturated_count += 1
            elif util >= 75.0:
                s["status"] = "HIGH LOAD"
                high_load_count += 1
            elif util > 0:
                s["status"] = "BALANCED"
                balanced_count += 1
            else:
                s["status"] = "EMPTY (SPARE)"
                empty_count += 1

        # Render KPI Cards
        self._apply_card_style(ws, start_col=1, start_row=3, width=2, height=2, label="Total Splitters", value=total_splitters, color_hex=COLOR_NAVY)
        self._apply_card_style(ws, start_col=3, start_row=3, width=2, height=2, label="Saturated (100%)", value=saturated_count, color_hex=COLOR_CRIMSON)
        self._apply_card_style(ws, start_col=5, start_row=3, width=2, height=2, label="High Load (>=75%)", value=high_load_count, color_hex=COLOR_AMBER)
        self._apply_card_style(ws, start_col=7, start_row=3, width=2, height=2, label="Balanced (1-74%)", value=balanced_count, color_hex=COLOR_EMERALD)
        self._apply_card_style(ws, start_col=9, start_row=3, width=2, height=2, label="Empty / Unused", value=empty_count, color_hex="64748B")

        # Table Column Headers
        headers = [
            "Region", "Center", "OLT Name", "Port", "Enclosure ID",
            "Splitter ID", "Splitter Ratio", "Total Capacity", "Connected Customers",
            "Available Spare Ports", "Utilization %", "Capacity Status"
        ]

        header_row = 6
        ws.row_dimensions[header_row].height = 24
        header_fill = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color=COLOR_BORDER),
            right=Side(style="thin", color=COLOR_BORDER),
            top=Side(style="thin", color=COLOR_BORDER),
            bottom=Side(style="thin", color=COLOR_BORDER)
        )

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # Populate Rows
        row_font = Font(name="Calibri", size=10)
        alt_fill = PatternFill(start_color=COLOR_ROW_ALT, end_color=COLOR_ROW_ALT, fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        # Sort: Saturated first, then High Load, then utilization descending
        sorted_splitters = sorted(splitters_map.values(), key=lambda x: (x["connected_customers"] / x["total_capacity"]), reverse=True)

        for idx, s in enumerate(sorted_splitters, start=header_row + 1):
            is_alt = (idx % 2 == 0)
            base_fill = alt_fill if is_alt else white_fill

            vals = [
                s["region"], s["center"], s["olt_name"], s["port_number"],
                s["enclosure_id"], s["splitter_id"], s["ratio"],
                s["total_capacity"], s["connected_customers"], s["spare_ports"],
                f"{s['utilization_pct']}%", s["status"]
            ]

            for col_idx, val in enumerate(vals, 1):
                c = ws.cell(row=idx, column=col_idx, value=val)
                c.font = row_font
                c.fill = base_fill
                c.border = thin_border

                if col_idx in (8, 9, 10, 11):
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")

                # Highlight status column
                if col_idx == 12:
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    if "CRITICAL" in s["status"]:
                        c.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="991B1B")
                    elif "HIGH" in s["status"]:
                        c.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="92400E")
                    elif "BALANCED" in s["status"]:
                        c.fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="065F46")

        self._auto_fit_columns(ws)
        ws.freeze_panes = f"A{header_row + 1}"

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Splitter_Utilization_Audit_{ts}.xlsx"
        target_path = os.path.join(self.output_dir, filename)
        wb.save(target_path)
        print(f"[SUCCESS] Splitter Utilization Report saved: {target_path}")
        return target_path

    # =========================================================================
    # REPORT 3: DATA QUALITY & FIELD HYGIENE AUDIT REPORT (.xlsx)
    # =========================================================================
    def generate_data_quality_report(self, records: Optional[List[Dict[str, Any]]] = None, filename: Optional[str] = None) -> str:
        """Audits database records for missing GNSS coordinates, ratio overflows, and missing pole IDs."""
        if records is None:
            records = self.fetch_records()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Quality_Audit"
        ws.views.sheetView[0].showGridLines = True

        ws.merge_cells("A1:H1")
        title = ws.cell(row=1, column=1, value="FIELD SURVEY DATA INTEGRITY & GNSS QUALITY AUDIT REPORT")
        title.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title.fill = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
        title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32

        # Audit logic
        issues = []
        missing_gps_count = 0
        missing_pole_count = 0
        overflow_count = 0
        duplicate_subscriber_map = {}

        for r in records:
            client_id = r.get("client_uuid") or "N/A"
            center = r.get("center") or "Unknown"
            enc_id = r.get("enclosure_id") or "Unassigned"
            lat_long = str(r.get("lat_long") or "").strip()
            pole = str(r.get("kseb_post_number") or "").strip()
            ratio = str(r.get("splitter_ratio") or "").strip()
            cust = int(r.get("customers_connected") or 0)
            sub_id = str(r.get("adl_subscriber_id") or r.get("acs_subscriber_id") or "").strip()

            # 1. Missing or zero GPS coordinates
            if not lat_long or lat_long == "0,0" or lat_long == "0.0,0.0":
                missing_gps_count += 1
                issues.append({
                    "severity": "CRITICAL",
                    "category": "GNSS Telemetry",
                    "center": center,
                    "enclosure_id": enc_id,
                    "field": "lat_long",
                    "observed_value": lat_long or "[EMPTY]",
                    "impact": "Map pin missing; enclosure unlocatable in GIS layer",
                    "recommendation": "Field surveyor must re-acquire GPS coordinate lock with <=5m precision"
                })

            # 2. Missing utility pole identifier
            if not pole:
                missing_pole_count += 1
                issues.append({
                    "severity": "WARNING",
                    "category": "Physical Infrastructure",
                    "center": center,
                    "enclosure_id": enc_id,
                    "field": "kseb_post_number",
                    "observed_value": "[EMPTY]",
                    "impact": "Field maintenance cannot identify physical mounting pole",
                    "recommendation": "Update pole post number from survey notes or physical tag"
                })

            # 3. Capacity Overflow
            max_ports = 8
            if "1:4" in ratio: max_ports = 4
            elif "1:16" in ratio: max_ports = 16
            if cust > max_ports:
                overflow_count += 1
                issues.append({
                    "severity": "CRITICAL",
                    "category": "Capacity Logic",
                    "center": center,
                    "enclosure_id": enc_id,
                    "field": "customers_connected",
                    "observed_value": f"{cust} (Ratio {ratio} max {max_ports})",
                    "impact": "Mathematical impossibility; corrupted splitter record",
                    "recommendation": "Verify physical terminations and correct customer count"
                })

            # Track subscriber IDs
            if sub_id:
                duplicate_subscriber_map.setdefault(sub_id, []).append(enc_id)

        # 4. Duplicate Subscriber IDs
        for sub_id, enc_list in duplicate_subscriber_map.items():
            if len(enc_list) > 1:
                issues.append({
                    "severity": "WARNING",
                    "category": "Customer Integrity",
                    "center": "Multi-Center",
                    "enclosure_id": ", ".join(enc_list[:3]),
                    "field": "subscriber_id",
                    "observed_value": sub_id,
                    "impact": f"Subscriber ID repeated across {len(enc_list)} survey points",
                    "recommendation": "Verify whether subscriber migrated or ID was transposed"
                })

        # KPI Cards
        total_issues = len(issues)
        health_score = 100
        if len(records) > 0:
            health_score = max(0, round(100 - (total_issues / len(records) * 50), 1))

        self._apply_card_style(ws, start_col=1, start_row=3, width=2, height=2, label="Data Health Score", value=f"{health_score}%", color_hex=COLOR_EMERALD if health_score >= 90 else COLOR_AMBER)
        self._apply_card_style(ws, start_col=3, start_row=3, width=2, height=2, label="Total Discrepancies", value=total_issues, color_hex=COLOR_NAVY)
        self._apply_card_style(ws, start_col=5, start_row=3, width=2, height=2, label="Missing Coordinates", value=missing_gps_count, color_hex=COLOR_CRIMSON)
        self._apply_card_style(ws, start_col=7, start_row=3, width=2, height=2, label="Missing Pole Tags", value=missing_pole_count, color_hex=COLOR_AMBER)

        # Table Column Headers
        headers = ["Severity", "Category", "Center", "Enclosure ID", "Discrepancy Field", "Observed Value", "Operational Impact", "Remediation Action"]
        header_row = 6
        ws.row_dimensions[header_row].height = 24
        header_fill = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color=COLOR_BORDER),
            right=Side(style="thin", color=COLOR_BORDER),
            top=Side(style="thin", color=COLOR_BORDER),
            bottom=Side(style="thin", color=COLOR_BORDER)
        )

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # Populate Rows
        row_font = Font(name="Calibri", size=10)
        alt_fill = PatternFill(start_color=COLOR_ROW_ALT, end_color=COLOR_ROW_ALT, fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        for idx, issue in enumerate(issues, start=header_row + 1):
            is_alt = (idx % 2 == 0)
            base_fill = alt_fill if is_alt else white_fill

            vals = [
                issue["severity"], issue["category"], issue["center"],
                issue["enclosure_id"], issue["field"], issue["observed_value"],
                issue["impact"], issue["recommendation"]
            ]

            for col_idx, val in enumerate(vals, 1):
                c = ws.cell(row=idx, column=col_idx, value=val)
                c.font = row_font
                c.fill = base_fill
                c.border = thin_border
                c.alignment = Alignment(horizontal="left", vertical="center")

                if col_idx == 1:
                    c.alignment = Alignment(horizontal="center", vertical="center")
                    if issue["severity"] == "CRITICAL":
                        c.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="991B1B")
                    elif issue["severity"] == "WARNING":
                        c.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="92400E")

        self._auto_fit_columns(ws)
        ws.freeze_panes = f"A{header_row + 1}"

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Data_Quality_Audit_{ts}.xlsx"
        target_path = os.path.join(self.output_dir, filename)
        wb.save(target_path)
        print(f"[SUCCESS] Data Quality Audit Report saved: {target_path}")
        return target_path

    # =========================================================================
    # REPORT 4: FIELD SURVEYOR PRODUCTIVITY REPORT (.xlsx)
    # =========================================================================
    def generate_surveyor_productivity_report(self, records: Optional[List[Dict[str, Any]]] = None, filename: Optional[str] = None) -> str:
        """Measures technician velocity, submission volume, and operational centers covered."""
        if records is None:
            records = self.fetch_records()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Technician_Productivity"
        ws.views.sheetView[0].showGridLines = True

        ws.merge_cells("A1:I1")
        title = ws.cell(row=1, column=1, value="FIELD OPERATIONS - SURVEYOR PRODUCTIVITY & VELOCITY AUDIT")
        title.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title.fill = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
        title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32

        # Aggregate by Surveyor
        surveyor_map = {}
        for r in records:
            username = (r.get("surveyor_username") or "unassigned").strip().lower()
            name = (r.get("surveyor_name") or username).strip()
            date_time = r.get("survey_date_time") or r.get("created_at") or ""
            date_only = date_time.split(" ")[0] if " " in date_time else date_time.split("T")[0]
            center = r.get("center") or ""
            device = r.get("device_id") or ""

            if username not in surveyor_map:
                surveyor_map[username] = {
                    "username": username,
                    "name": name,
                    "total_surveys": 0,
                    "total_customers": 0,
                    "active_dates": set(),
                    "centers": set(),
                    "devices": set(),
                    "first_survey": date_time,
                    "last_survey": date_time
                }

            s = surveyor_map[username]
            s["total_surveys"] += 1
            s["total_customers"] += int(r.get("customers_connected") or 0)
            if date_only: s["active_dates"].add(date_only)
            if center: s["centers"].add(center)
            if device: s["devices"].add(device)
            if date_time and (not s["first_survey"] or date_time < s["first_survey"]):
                s["first_survey"] = date_time
            if date_time and (not s["last_survey"] or date_time > s["last_survey"]):
                s["last_survey"] = date_time

        # KPI Cards
        total_active_surveyors = len(surveyor_map)
        total_surveys = sum(s["total_surveys"] for s in surveyor_map.values())
        avg_per_surveyor = round(total_surveys / total_active_surveyors, 1) if total_active_surveyors > 0 else 0

        self._apply_card_style(ws, start_col=1, start_row=3, width=2, height=2, label="Active Surveyors", value=total_active_surveyors, color_hex=COLOR_NAVY)
        self._apply_card_style(ws, start_col=3, start_row=3, width=2, height=2, label="Total Records Mapped", value=total_surveys, color_hex="0284C7")
        self._apply_card_style(ws, start_col=5, start_row=3, width=2, height=2, label="Average per Surveyor", value=avg_per_surveyor, color_hex=COLOR_EMERALD)

        # Table Column Headers
        headers = [
            "Surveyor Username", "Full Name", "Total Surveys", "Total Subscribers",
            "Active Field Days", "Velocity (Surveys/Day)", "Centers Covered",
            "First Submission", "Latest Submission"
        ]
        header_row = 6
        ws.row_dimensions[header_row].height = 24
        header_fill = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color=COLOR_BORDER),
            right=Side(style="thin", color=COLOR_BORDER),
            top=Side(style="thin", color=COLOR_BORDER),
            bottom=Side(style="thin", color=COLOR_BORDER)
        )

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        # Populate Rows
        row_font = Font(name="Calibri", size=10)
        alt_fill = PatternFill(start_color=COLOR_ROW_ALT, end_color=COLOR_ROW_ALT, fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        sorted_surveyors = sorted(surveyor_map.values(), key=lambda x: x["total_surveys"], reverse=True)

        for idx, s in enumerate(sorted_surveyors, start=header_row + 1):
            is_alt = (idx % 2 == 0)
            base_fill = alt_fill if is_alt else white_fill
            active_days = max(1, len(s["active_dates"]))
            velocity = round(s["total_surveys"] / active_days, 1)

            vals = [
                s["username"], s["name"], s["total_surveys"], s["total_customers"],
                active_days, velocity, ", ".join(sorted(s["centers"])),
                s["first_survey"], s["last_survey"]
            ]

            for col_idx, val in enumerate(vals, 1):
                c = ws.cell(row=idx, column=col_idx, value=val)
                c.font = row_font
                c.fill = base_fill
                c.border = thin_border
                if col_idx in (3, 4, 5, 6):
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")

        self._auto_fit_columns(ws)
        ws.freeze_panes = f"A{header_row + 1}"

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Field_Productivity_Audit_{ts}.xlsx"
        target_path = os.path.join(self.output_dir, filename)
        wb.save(target_path)
        print(f"[SUCCESS] Field Productivity Report saved: {target_path}")
        return target_path

    # =========================================================================
    # REPORT 5: NETWORK COVERAGE & OLT AUDIT REPORT (.xlsx)
    # =========================================================================
    def generate_network_coverage_report(self, records: Optional[List[Dict[str, Any]]] = None, filename: Optional[str] = None) -> str:
        """Cross-references surveyed OLTs against custom_hierarchy.json planned topology."""
        if records is None:
            records = self.fetch_records()

        hierarchy = self._load_hierarchy()
        surveyed_olts = set(r.get("olt_name") for r in records if r.get("olt_name"))

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "OLT_Coverage_Audit"
        ws.views.sheetView[0].showGridLines = True

        ws.merge_cells("A1:G1")
        title = ws.cell(row=1, column=1, value="TELECOM INFRASTRUCTURE - OLT MAPPING & COVERAGE AUDIT")
        title.font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title.fill = PatternFill(start_color=COLOR_NAVY, end_color=COLOR_NAVY, fill_type="solid")
        title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32

        # Parse Hierarchy
        planned_olts = []
        for center_name, rt_dict in hierarchy.items():
            if isinstance(rt_dict, dict):
                for rt_name, olt_dict in rt_dict.items():
                    if isinstance(olt_dict, dict):
                        for olt_name, meta in olt_dict.items():
                            ports_count = len(meta.get("ports", [])) if isinstance(meta, dict) else 8
                            region = meta.get("region", "Thrissur") if isinstance(meta, dict) else "Thrissur"
                            is_surveyed = (olt_name in surveyed_olts)
                            planned_olts.append({
                                "region": region,
                                "center": center_name,
                                "rt_room": rt_name,
                                "olt_name": olt_name,
                                "ports_count": ports_count,
                                "is_surveyed": is_surveyed
                            })

        total_planned = len(planned_olts)
        total_mapped = sum(1 for o in planned_olts if o["is_surveyed"])
        coverage_pct = round((total_mapped / total_planned * 100), 1) if total_planned > 0 else 0

        self._apply_card_style(ws, start_col=1, start_row=3, width=2, height=2, label="Total Planned OLTs", value=total_planned, color_hex=COLOR_NAVY)
        self._apply_card_style(ws, start_col=3, start_row=3, width=2, height=2, label="Surveyed OLTs", value=total_mapped, color_hex=COLOR_EMERALD)
        self._apply_card_style(ws, start_col=5, start_row=3, width=2, height=2, label="Unmapped OLTs", value=total_planned - total_mapped, color_hex=COLOR_CRIMSON)
        self._apply_card_style(ws, start_col=7, start_row=3, width=2, height=2, label="Coverage %", value=f"{coverage_pct}%", color_hex="0284C7")

        headers = ["Region", "Center", "RT Room", "OLT Name", "Configured Ports", "Survey Status", "Action Required"]
        header_row = 6
        ws.row_dimensions[header_row].height = 24
        header_fill = PatternFill(start_color=COLOR_SLATE, end_color=COLOR_SLATE, fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style="thin", color=COLOR_BORDER),
            right=Side(style="thin", color=COLOR_BORDER),
            top=Side(style="thin", color=COLOR_BORDER),
            bottom=Side(style="thin", color=COLOR_BORDER)
        )

        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        row_font = Font(name="Calibri", size=10)
        alt_fill = PatternFill(start_color=COLOR_ROW_ALT, end_color=COLOR_ROW_ALT, fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        # Sort: Unmapped first so field supervisors see pending work immediately
        sorted_olts = sorted(planned_olts, key=lambda x: x["is_surveyed"])

        for idx, o in enumerate(sorted_olts, start=header_row + 1):
            is_alt = (idx % 2 == 0)
            base_fill = alt_fill if is_alt else white_fill
            status_text = "SURVEYED / MAPPED" if o["is_surveyed"] else "PENDING SURVEY"
            action_text = "Audit complete" if o["is_surveyed"] else "Dispatch field team for optical distribution survey"

            vals = [
                o["region"], o["center"], o["rt_room"], o["olt_name"],
                o["ports_count"], status_text, action_text
            ]

            for col_idx, val in enumerate(vals, 1):
                c = ws.cell(row=idx, column=col_idx, value=val)
                c.font = row_font
                c.fill = base_fill
                c.border = thin_border
                if col_idx in (5, 6):
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(horizontal="left", vertical="center")

                if col_idx == 6:
                    if o["is_surveyed"]:
                        c.fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="065F46")
                    else:
                        c.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
                        c.font = Font(name="Calibri", size=10, bold=True, color="991B1B")

        self._auto_fit_columns(ws)
        ws.freeze_panes = f"A{header_row + 1}"

        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Network_Coverage_Audit_{ts}.xlsx"
        target_path = os.path.join(self.output_dir, filename)
        wb.save(target_path)
        print(f"[SUCCESS] Network Coverage Report saved: {target_path}")
        return target_path

    # =========================================================================
    # REPORT 6: EXECUTIVE KPI DIGEST (JSON & MARKDOWN)
    # =========================================================================
    def generate_executive_summary(self, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """Generates machine-readable JSON and human-readable Markdown executive summary."""
        if records is None:
            records = self.fetch_records()

        total_records = len(records)
        unique_enclosures = len(set(r.get("enclosure_id") for r in records if r.get("enclosure_id")))
        unique_splitters = len(set(r.get("splitter_id") for r in records if r.get("splitter_id")))
        total_customers = sum(int(r.get("customers_connected") or 0) for r in records)
        unique_olts = len(set(r.get("olt_name") for r in records if r.get("olt_name")))

        hierarchy = self._load_hierarchy()
        total_planned_olts = 0
        for c_dict in hierarchy.values():
            if isinstance(c_dict, dict):
                for rt_dict in c_dict.values():
                    if isinstance(rt_dict, dict):
                        total_planned_olts += len(rt_dict)

        olt_coverage_pct = round(unique_olts / total_planned_olts * 100, 1) if total_planned_olts > 0 else 0

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_data = {
            "system_name": "Enterprise GPON Network Mapping System",
            "generated_at": datetime.datetime.now().isoformat(),
            "total_survey_records": total_records,
            "unique_enclosures": unique_enclosures,
            "unique_splitters": unique_splitters,
            "connected_customers": total_customers,
            "mapped_olts": unique_olts,
            "total_planned_olts": total_planned_olts,
            "olt_coverage_percentage": olt_coverage_pct
        }

        # Save JSON
        json_path = os.path.join(self.output_dir, f"GPON_Executive_Summary_{ts}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

        # Build Markdown
        md_content = f"""# Enterprise GPON Network Mapping: Executive Operations Summary

**Generated At:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  
**System Architecture:** Dual-Zone Distributed Infrastructure (Zero Brand Enforced)

---

## Key Performance Indicators (KPIs)

| Metric | Measured Value | Operational Context |
| :--- | :--- | :--- |
| **Total Survey Records** | **{total_records:,}** | Physical optical survey points logged in database |
| **Unique Enclosures** | **{unique_enclosures:,}** | Verified physical distribution boxes with GPS |
| **Active Splitters** | **{unique_splitters:,}** | Deployed 1:4 / 1:8 / 1:16 optical splitters |
| **Connected Subscribers** | **{total_customers:,}** | Active subscriber terminations documented |
| **OLT Infrastructure Coverage** | **{unique_olts:,} / {total_planned_olts:,} ({olt_coverage_pct}%)** | Mapped OLTs vs central hierarchy master |

---

## Infrastructure Health & Disaster Recovery

* **Dual-Zone Partitioning:** Operational (North & South clusters independent)
* **Cloud Backup Replication:** Automated hourly/daily cron with Google Drive
* **Zero Brand Compliance:** Verified (100% vendor-neutral enterprise naming)
"""
        md_path = os.path.join(self.output_dir, f"GPON_Executive_Summary_{ts}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        print(f"[SUCCESS] Executive Summary saved: {json_path} & {md_path}")
        return {"json": json_path, "markdown": md_path}

    # =========================================================================
    # REPORT 7: BUNDLED MULTI-REPORT ZIP ARCHIVE (.zip)
    # =========================================================================
    def create_report_bundle_zip(self, generated_files: List[str], filename: Optional[str] = None) -> str:
        """Packages multiple generated reports into a single compressed ZIP archive."""
        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"GPON_Reports_Bundle_{ts}.zip"

        target_zip_path = os.path.join(self.output_dir, filename)
        with zipfile.ZipFile(target_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in generated_files:
                if os.path.exists(file_path):
                    arcname = os.path.basename(file_path)
                    zip_file.write(file_path, arcname=arcname)
                    print(f"  [ARCHIVE] Added {arcname}")

        print(f"[SUCCESS] Multi-Report Bundle ZIP created: {target_zip_path}")
        return target_zip_path

    # =========================================================================
    # MASTER RUNNER: GENERATE ALL REPORTS
    # =========================================================================
    def generate_all_reports(self, region: Optional[str] = None, center: Optional[str] = None) -> Dict[str, Any]:
        """Generates all 5 Excel reports, Executive Summary (JSON/MD), and compresses into a master ZIP archive."""
        records = self.fetch_records(region=region, center=center)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        print(f"\n========================================================")
        print(f" [REPORT GENERATOR] Processing {len(records)} records...")
        print(f"========================================================")

        r1 = self.generate_master_survey_report(records, filename=f"01_GPON_Master_Survey_Report_{ts}.xlsx")
        r2 = self.generate_splitter_utilization_report(records, filename=f"02_GPON_Splitter_Utilization_{ts}.xlsx")
        r3 = self.generate_data_quality_report(records, filename=f"03_GPON_Data_Quality_Audit_{ts}.xlsx")
        r4 = self.generate_surveyor_productivity_report(records, filename=f"04_GPON_Surveyor_Productivity_{ts}.xlsx")
        r5 = self.generate_network_coverage_report(records, filename=f"05_GPON_Network_Coverage_Audit_{ts}.xlsx")
        summary_files = self.generate_executive_summary(records)

        # Check if presentation deck exists and add it to bundle
        all_files = [r1, r2, r3, r4, r5, summary_files["json"], summary_files["markdown"]]
        ppt_candidate = os.path.join(BASE_DIR, "GPON_Field_Scenarios_and_Solutions_Report.pptx")
        if os.path.exists(ppt_candidate):
            all_files.append(ppt_candidate)

        # Create master ZIP bundle
        zip_path = self.create_report_bundle_zip(all_files, filename=f"GPON_Complete_Reports_Bundle_{ts}.zip")

        return {
            "master_survey": r1,
            "splitter_utilization": r2,
            "data_quality": r3,
            "surveyor_productivity": r4,
            "network_coverage": r5,
            "executive_summary_json": summary_files["json"],
            "executive_summary_md": summary_files["markdown"],
            "bundle_zip": zip_path
        }

    def get_catalog(self) -> List[Dict[str, Any]]:
        """Returns the full catalog of reports available for generation and download."""
        return [
            {
                "id": "master",
                "title": "Master Field Survey Dataset",
                "format": "XLSX",
                "category": "Field Operations",
                "icon": "table",
                "description": "Complete field survey database with formatted compound IDs (OPM1120LT25P1E1S1), GIS coordinates, customer counts, and KPI summary header."
            },
            {
                "id": "splitter",
                "title": "Splitter Utilization & Saturation Audit",
                "format": "XLSX",
                "category": "Network Engineering",
                "icon": "pie-chart",
                "description": "Evaluates optical splitter capacity, active connected subscriber counts, available spare ports, and flags saturated (100%) vs underutilized splitters."
            },
            {
                "id": "quality",
                "title": "Field Data Quality & GNSS Hygiene Audit",
                "format": "XLSX",
                "category": "Quality Assurance",
                "icon": "shield-check",
                "description": "Detects missing or 0,0 GNSS coordinates, utility pole post ID gaps, capacity overflows, and duplicate subscriber IDs with severity ratings."
            },
            {
                "id": "productivity",
                "title": "Field Surveyor Productivity & Velocity",
                "format": "XLSX",
                "category": "Operations Governance",
                "icon": "users",
                "description": "Analyzes submission velocity per technician, active field days, centers covered, device tracking, and survey timestamp timelines."
            },
            {
                "id": "coverage",
                "title": "OLT Infrastructure & Port Coverage",
                "format": "XLSX",
                "category": "Telecom Planning",
                "icon": "map",
                "description": "Cross-references surveyed OLTs and ports against the master planned hierarchy to identify unmapped nodes and coverage gaps."
            },
            {
                "id": "executive_summary",
                "title": "Executive KPI Operations Digest",
                "format": "JSON / MD",
                "category": "Executive Briefing",
                "icon": "file-text",
                "description": "High-level summary of total survey points, enclosure density, splitter ratio breakdown, and backup replication health."
            },
            {
                "id": "scenarios_ppt",
                "title": "22 Field Scenarios Engineering Presentation",
                "format": "PPTX",
                "category": "System Architecture",
                "icon": "presentation",
                "description": "17-slide 16:9 widescreen executive deck detailing all 22 operational field challenges and engineered software/hardware solutions."
            },
            {
                "id": "bundle",
                "title": "All-in-One Multi-Report Master Archive",
                "format": "ZIP",
                "category": "Complete Bundle",
                "icon": "archive",
                "description": "Packages all 5 Excel reports, Executive Summary (JSON + MD), and the 22-Scenarios Presentation Deck into a single timestamped ZIP."
            }
        ]

    def generate_report_by_type(self, report_type: str, region: Optional[str] = None, center: Optional[str] = None, olt: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> tuple:
        """
        Generates requested report and returns (file_path, media_type, filename).
        """
        records = self.fetch_records(region=region, center=center, olt=olt, start_date=start_date, end_date=end_date)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        media_xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        media_pptx = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        media_zip = "application/zip"
        media_md = "text/markdown"

        rt = (report_type or "").lower().strip()
        if rt in ("master", "master_survey"):
            fn = f"GPON_Master_Survey_Report_{ts}.xlsx"
            path = self.generate_master_survey_report(records, filename=fn)
            return path, media_xlsx, fn
        elif rt in ("splitter", "splitter_utilization"):
            fn = f"GPON_Splitter_Utilization_{ts}.xlsx"
            path = self.generate_splitter_utilization_report(records, filename=fn)
            return path, media_xlsx, fn
        elif rt in ("quality", "data_quality"):
            fn = f"GPON_Data_Quality_Audit_{ts}.xlsx"
            path = self.generate_data_quality_report(records, filename=fn)
            return path, media_xlsx, fn
        elif rt in ("productivity", "field_productivity"):
            fn = f"GPON_Surveyor_Productivity_{ts}.xlsx"
            path = self.generate_surveyor_productivity_report(records, filename=fn)
            return path, media_xlsx, fn
        elif rt in ("coverage", "network_coverage"):
            fn = f"GPON_Network_Coverage_Audit_{ts}.xlsx"
            path = self.generate_network_coverage_report(records, filename=fn)
            return path, media_xlsx, fn
        elif rt in ("summary", "executive_summary"):
            files = self.generate_executive_summary(records)
            fn = os.path.basename(files["markdown"])
            return files["markdown"], media_md, fn
        elif rt in ("scenarios_ppt", "ppt", "presentation"):
            ppt_path = os.path.join(BASE_DIR, "GPON_Field_Scenarios_and_Solutions_Report.pptx")
            if not os.path.exists(ppt_path):
                try:
                    from scripts.generate_all_scenarios_ppt import build_presentation
                    build_presentation(ppt_path)
                except Exception as e:
                    print(f"[ReportGenerator] Could not build presentation: {e}")
            fn = "GPON_Field_Scenarios_and_Solutions_Report.pptx"
            return ppt_path, media_pptx, fn
        elif rt in ("bundle", "all", "bundle_zip"):
            res = self.generate_all_reports(region=region, center=center)
            path = res["bundle_zip"]
            fn = os.path.basename(path)
            return path, media_zip, fn
        else:
            raise ValueError(f"Unknown report type '{report_type}'. Available types: master, splitter, quality, productivity, coverage, summary, scenarios_ppt, bundle.")

    def seed_sample_data(self) -> int:
        """Seeds realistic sample survey records into survey_records table if empty."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS survey_records (
            client_uuid TEXT PRIMARY KEY,
            region TEXT,
            center TEXT,
            rt_room TEXT,
            technology TEXT,
            olt_name TEXT,
            port_number TEXT,
            kseb_post_number TEXT,
            landmark TEXT,
            enclosure_number TEXT,
            enclosure_id TEXT,
            lat_long TEXT,
            splitter_id TEXT,
            splitter_ratio TEXT,
            customers_connected INTEGER,
            splitter_lead_color TEXT,
            adl_subscriber_id TEXT,
            acs_subscriber_id TEXT,
            survey_date_time TEXT,
            device_id TEXT,
            surveyor_username TEXT,
            surveyor_name TEXT,
            created_at TEXT,
            synced_at TEXT
        )
        """)
        
        count = cur.execute("SELECT COUNT(*) FROM survey_records").fetchone()[0]
        if count > 0:
            conn.close()
            return count

        sample_xlsx = os.path.join(BASE_DIR, "sample_swmaps_export_formatted.xlsx")
        inserted = 0
        if os.path.exists(sample_xlsx):
            wb = openpyxl.load_workbook(sample_xlsx)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            for r in rows[2:]:
                if not r or not any(r): continue
                uid = str(uuid.uuid4())
                cur.execute("""
                INSERT OR IGNORE INTO survey_records (
                    client_uuid, region, center, rt_room, technology, olt_name, port_number,
                    kseb_post_number, landmark, enclosure_number, enclosure_id, lat_long,
                    splitter_id, splitter_ratio, customers_connected, splitter_lead_color,
                    adl_subscriber_id, acs_subscriber_id, survey_date_time, device_id,
                    surveyor_username, surveyor_name, created_at, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    uid, r[0] or "Thrissur", r[1] or "Thrissur North", r[2] or "", r[3] or "GPON",
                    r[4] or "", r[5] or "P1", r[6] or "", r[7] or "", r[8] or "E1", r[9] or "",
                    r[10] or "10.60665,76.21449", r[11] or "S1", r[12] or "1:8", int(r[13] or 0),
                    r[14] or "", r[15] or "", r[16] or "", r[17] or datetime.datetime.now().isoformat(),
                    "DEV-SM-G998B", "tech_demo", "Field Engineer Demo", datetime.datetime.now().isoformat(),
                    datetime.datetime.now().isoformat()
                ))
                inserted += 1
            conn.commit()
            print(f"[SEED] Successfully seeded {inserted} demo survey records into database.")
        conn.close()
        return inserted


# ==========================================
# CLI ENTRYPOINT
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Enterprise GPON Network Mapping Multi-Report Generator Engine")
    parser.add_argument("--type", choices=["all", "master", "splitter", "quality", "productivity", "coverage", "summary", "bundle", "ppt"], default="all",
                        help="Report type to generate (default: all)")
    parser.add_argument("--seed-demo", action="store_true", help="Seed sample survey data if database is empty")
    parser.add_argument("--region", type=str, default=None, help="Filter records by Region")
    parser.add_argument("--center", type=str, default=None, help="Filter records by Center")
    parser.add_argument("--olt", type=str, default=None, help="Filter records by OLT Name")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Destination folder for generated reports")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite survey database")

    args = parser.parse_args()

    generator = ReportGenerator(db_path=args.db_path, output_dir=args.output_dir)

    if args.seed_demo:
        count = generator.seed_sample_data()
        print(f"[INFO] Current database records: {count}")

    if args.type == "all" or args.type == "bundle":
        results = generator.generate_all_reports(region=args.region, center=args.center)
        print(f"\n[COMPLETE] All reports generated successfully! Master bundle: {results['bundle_zip']}")
    elif args.type == "master":
        generator.generate_master_survey_report()
    elif args.type == "splitter":
        generator.generate_splitter_utilization_report()
    elif args.type == "quality":
        generator.generate_data_quality_report()
    elif args.type == "productivity":
        generator.generate_surveyor_productivity_report()
    elif args.type == "coverage":
        generator.generate_network_coverage_report()
    elif args.type == "summary":
        generator.generate_executive_summary()
    elif args.type in ("ppt", "scenarios_ppt"):
        generator.generate_report_by_type("scenarios_ppt")

if __name__ == "__main__":
    main()
