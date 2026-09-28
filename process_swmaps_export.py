"""
SW Maps to GPON OLT Mapping Excel/CSV Converter
Converts raw SW Maps CSV export into the official GPON OLT MAPPING TCR format.
"""

import os
import re
import glob
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

def generate_enclosure_id(olt_name, port, enclosure):
    """
    Auto-generates Enclosure ID from OLT Name, Port, and Enclosure Number.
    Examples:
      'THN156 OLT53 Mulamkunnathukavu', 'P1', 'E1' -> 'THN156OLT53P1E1'
      'TMM/25/OLT-08-KOLLEMGODE', 'P2', 'E1'       -> 'TMM25OLT08P2E1'
    """
    if not olt_name:
        olt_part = ""
    else:
        # Match pattern like THN156 OLT53 or TMM/25/OLT-08
        match = re.search(r'([A-Za-z]+[\s\/\-_]*\d+[\s\/\-_]*OLT[\s\/\-_]*\d+)', str(olt_name), re.IGNORECASE)
        if match:
            olt_part = re.sub(r'[\s\/\-_]', '', match.group(1)).upper()
        else:
            # Fallback: remove symbols and take first 15 chars
            clean = re.sub(r'[\s\/\-_]', '', str(olt_name)).upper()
            olt_part = clean[:12]

    port_part = str(port).strip().upper() if port else ""
    enc_part = str(enclosure).strip().upper() if enclosure else ""
    
    return f"{olt_part}{port_part}{enc_part}"

def process_swmaps_file(input_csv_path, output_xlsx_path=None, output_csv_path=None):
    if not os.path.exists(input_csv_path):
        raise FileNotFoundError(f"Input file not found: {input_csv_path}")

    # Read SW Maps CSV
    df_raw = pd.read_csv(input_csv_path)
    
    # Normalize column names for robust matching (lowercase, no spaces/underscores)
    col_map = {}
    for col in df_raw.columns:
        norm = re.sub(r'[^a-zA-Z0-9]', '', col).lower()
        col_map[norm] = col

    def get_val(row, *aliases, default=""):
        for alias in aliases:
            norm_alias = re.sub(r'[^a-zA-Z0-9]', '', alias).lower()
            if norm_alias in col_map:
                val = row[col_map[norm_alias]]
                if pd.notna(val) and str(val).strip() != "":
                    return str(val).strip()
        return default

    processed_rows = []
    
    for _, row in df_raw.iterrows():
        # Coordinates
        lat = get_val(row, "latitude", "lat")
        lon = get_val(row, "longitude", "long", "lon")
        
        try:
            lat_f = f"{float(lat):.5f}"
            lon_f = f"{float(lon):.5f}"
            lat_long_str = f"{lat_f},{lon_f}"
        except (ValueError, TypeError):
            lat_long_str = f"{lat},{lon}" if lat and lon else ""

        region = get_val(row, "region", default="Thrissur")
        center = get_val(row, "center", default="Thrissur North")
        rt_room = get_val(row, "rtroom", "rt_room", default="Mulamkunnathukavu")
        tech = get_val(row, "gponftthwdm", "technology", "tech", default="GPON")
        olt = get_val(row, "oltnodename", "olt_name", "olt")
        port = get_val(row, "portnumber", "port_no", "port", default="P1")
        kseb_post = get_val(row, "ksebpostnumber", "kseb_post", "post_no", "kseb")
        landmark = get_val(row, "landmark", "land_mark")
        enclosure = get_val(row, "enclosurenumber", "enclosure_no", "enclosure", default="E1")
        
        # Enclosure ID auto-generated
        enclosure_id = get_val(row, "enclosureid", "enclosure_id")
        if not enclosure_id:
            enclosure_id = generate_enclosure_id(olt, port, enclosure)

        splitter_id = get_val(row, "splitterid", "splitter_id", default="S1")
        splitter_ratio = get_val(row, "splitterratio", "splitter_ratio", default="1:8")
        
        cust_str = get_val(row, "noofcustomerconnected", "customers_connected", "customers", default="0")
        try:
            cust_connected = int(float(cust_str))
        except (ValueError, TypeError):
            cust_connected = 0

        splitter_lead_color = get_val(row, "splitterleadcolourcode", "splitterleadcolorcode", "leadcolor", "colourcode", "colorcode", default="")
        adl_sub_id = get_val(row, "adlsubscriberid", "adlsubid", "adlid", default="")
        acs_sub_id = get_val(row, "acssubscriberid", "acssubid", "acsid", default="")
        date_time = get_val(row, "dateandtime", "datetime", "timestamp", "time", "date", default="")

        processed_rows.append({
            "Region": region,
            "Center": center,
            "RT Room": rt_room,
            "GPON/FTTH/WDM": tech,
            "OLT/Node  Name": olt,
            "Port Number": port,
            "KSEB Post Number": kseb_post,
            "Land Mark": landmark,
            "Enclosure Number": enclosure,
            "Enclosure ID": enclosure_id,
            "Lat /Long": lat_long_str,
            "Splitter ID": splitter_id,
            "Splitter Ratio": splitter_ratio,
            "No: Of Customer Connected": cust_connected,
            "Splitter Lead Colour Code": splitter_lead_color,
            "ADL Subscriber ID": adl_sub_id,
            "ACS Subscriber ID": acs_sub_id,
            "Date & Time": date_time
        })

    df_out = pd.DataFrame(processed_rows)

    # Output paths
    base_name = os.path.splitext(input_csv_path)[0]
    if not output_xlsx_path:
        output_xlsx_path = f"{base_name}_formatted.xlsx"
    if not output_csv_path:
        output_csv_path = f"{base_name}_formatted.csv"

    # Save to CSV
    df_out.to_csv(output_csv_path, index=False)
    print(f"[OK] Exported CSV to: {output_csv_path}")

    # Save to Excel with professional styling matching template
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # Header in Row 2 (Row 1 empty or title like original)
    headers = list(df_out.columns)
    
    header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="000000")
    thin_border = Border(
        left=Side(style="thin", color="BFBFBF"),
        right=Side(style="thin", color="BFBFBF"),
        top=Side(style="thin", color="BFBFBF"),
        bottom=Side(style="thin", color="BFBFBF")
    )
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=2, column=col_idx, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    # Data Rows starting Row 3
    for row_idx, r_data in enumerate(processed_rows, 3):
        for col_idx, h in enumerate(headers, 1):
            val = r_data[h]
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=10)
            cell.border = thin_border
            
            if h in ["Region", "Center", "RT Room", "GPON/FTTH/WDM", "Port Number", "Enclosure Number", "Splitter ID", "Splitter Ratio", "No: Of Customer Connected"]:
                cell.alignment = center_align
            else:
                cell.alignment = left_align

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = col[1].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    wb.save(output_xlsx_path)
    print(f"[OK] Exported Excel to: {output_xlsx_path}")
    print(f"[OK] Total rows processed: {len(processed_rows)}")
    return output_xlsx_path, output_csv_path

if __name__ == "__main__":
    import sys
    # Find any swmaps export csv in current directory if not specified
    if len(sys.argv) > 1:
        target_csv = sys.argv[1]
    else:
        candidates = glob.glob("*swmaps*.csv") or glob.glob("*.csv")
        target_csv = candidates[0] if candidates else None

    if target_csv and os.path.exists(target_csv):
        print(f"Processing: {target_csv}")
        process_swmaps_file(target_csv)
    else:
        print("Usage: python process_swmaps_export.py <swmaps_export.csv>")
