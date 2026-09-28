# SW Maps Field Survey Guide for GPON Network Mapping

This guide provides step-by-step instructions for field technicians and survey engineers to collect GPON / FTTH enclosure and pole data on mobile devices using **SW Maps**, and automatically convert the results into the standard **GPON OLT MAPPING Excel / CSV** format.

---

## 1. App Installation
1. Install **SW Maps - Field GIS & GNSS** by *Softwel* from the Google Play Store (Android) or App Store (iOS).
2. Open the app and grant **Location** and **Storage / Camera** permissions.

---

## 2. One-Time Setup in SW Maps (Takes 3 Minutes)

### Step 2.1: Create a Project
1. Open the side menu (top-left ☰) and tap **Projects**.
2. Tap **New Project**.
3. Name it: `GPON_Network_Survey` (or your area name, e.g., `GPON_Thrissur`).
4. Tap **Create**.

### Step 2.2: Add the Point Feature Layer
1. Open the side menu ☰ and tap **Layers**.
2. Tap the **+** (Add Layer) button at the bottom.
3. Choose **Feature Layer (Point)**.
4. Name the layer: `GPON_Enclosures`.
5. Tap **Add Attributes** to configure the fields as shown in the table below.

---

## 3. Attribute Configuration Table

Configure each field in SW Maps with the following settings:

| # | Attribute Name | Data Type | Preload Dropdown Options / Description |
|---|---|---|---|
| 1 | `Region` | **Options** | `Thrissur, Palakkad, Ernakulam` |
| 2 | `Center` | **Options** | `Thrissur North, Thathamangalam` |
| 3 | `RT_Room` | **Options** | `Mulamkunnathukavu, Kollengode` |
| 4 | `GPON_FTTH_WDM` | **Options** | `GPON, FTTH, WDM` |
| 5 | `OLT_Node_Name` | **Options** | `THN156 OLT53 Mulamkunnathukavu, TMM/25/OLT-08-KOLLEMGODE` |
| 6 | `Port_Number` | **Options** | `P1, P2, P3, P4, P5, P6, P7, P8, P16` |
| 7 | `KSEB_Post_Number` | **Text** | Technician types post number *(e.g., `MAT 148/9`)* |
| 8 | `Land_Mark` | **Text** | Technician types landmark *(e.g., `NR RT ROOM`)* |
| 9 | `Enclosure_Number` | **Options** | `E1, E2, E3, E4, E5, E6, E7, E8, E9, E10, E11, E12` |
| 10 | `Splitter_ID` | **Options** | `S1, S2, S3, S4` |
| 11 | `Splitter_Ratio` | **Options** | `1:8, 1:4, 1:16, 1:32` |
| 12 | `No_Of_Customer_Connected` | **Numeric** | Integer *(0, 1, 2, 3...)* |
| 13 | `Photo` *(Optional)* | **Photo** | Tap camera to snap a photo of the pole & enclosure box |

> **Tip:** In SW Maps, when you select the **Options** type, simply paste the comma-separated options into the list.

---

## 4. How to Survey in the Field

1. **Turn on Mobile GPS** in high-accuracy mode.
2. Open **SW Maps** and load your project.
3. Stand right beside the KSEB electric post where the enclosure is mounted.
4. Tap the **Record Feature** icon (the crosshair/plus icon on the map).
   - Your exact **Latitude** and **Longitude** are captured automatically.
5. Select the dropdowns:
   - Select `Center`, `RT_Room`, `OLT_Node_Name`, `Port_Number`, `Enclosure_Number`, `Splitter_Ratio`.
6. Enter the manual fields:
   - Type the `KSEB_Post_Number` (e.g., `MAT 148/9`).
   - Type the `Land_Mark` (e.g., `NR RT ROOM`).
   - Enter `No_Of_Customer_Connected`.
7. (Optional) Tap **Take Photo** to capture an image of the enclosure and cable loop.
8. Tap **Save**. The point is plotted on your map!

---

## 5. Exporting from Mobile Device

At the end of the survey day:
1. In SW Maps, open the side menu ☰.
2. Tap **Export**.
3. Choose **Export as CSV** (or **Spreadsheet**).
4. Share or transfer the exported `.csv` file to your PC (via WhatsApp, Google Drive, USB, or Email).

---

## 6. One-Click Conversion to Final GPON Excel / CSV Format

A dedicated conversion script is prepared in this workspace:
- **`process_swmaps_export.py`**
- **`Run_SW_Maps_Converter.bat`**

### Steps to Convert:
1. Place the exported CSV file from SW Maps into this folder (`D:\GPON Network Mapping Data`).
2. Simply **double-click** `Run_SW_Maps_Converter.bat` (or drag and drop your CSV file directly onto `Run_SW_Maps_Converter.bat`).
3. The converter will automatically:
   - Extract and format GPS coordinates into `Lat /Long` (e.g., `10.60665,76.21449`).
   - Automatically calculate the **Enclosure ID** (e.g., `THN156OLT53P1E1` or `TMM25OLT08P2E1`).
   - Format all columns and styling identically to `GPON OLT MAPPING TCR.xlsx`.
   - Generate both:
     - `..._formatted.xlsx`
     - `..._formatted.csv`
