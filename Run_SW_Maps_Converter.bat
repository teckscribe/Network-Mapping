@echo off
title SW Maps to GPON OLT Mapping Converter
echo ========================================================
echo   GPON Network Mapping - SW Maps CSV to Excel Converter
echo ========================================================
echo.

if "%~1"=="" (
    echo Searching for the latest SW Maps CSV export in this folder...
    python process_swmaps_export.py
) else (
    echo Processing file: %~1
    python process_swmaps_export.py "%~1"
)

echo.
echo ========================================================
echo Conversion Complete!
echo You will find the formatted .xlsx and .csv files above.
echo ========================================================
pause
