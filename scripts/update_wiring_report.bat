@echo off
REM ========================================================================
REM  GPON Network Mapping - Automated Wiring Report Generator (Windows)
REM ========================================================================
echo [INFO] Scanning codebase and updating architecture wiring reports...
python "%~dp0generate_wiring_report.py"
if %ERRORLEVEL% equ 0 (
    echo [SUCCESS] Wiring reports and interactive portal updated successfully!
    echo [PATH] Markdown reports: docs\wiring\
    echo [PATH] Interactive portal: web_app\wiring_report.html
) else (
    echo [ERROR] Failed to generate wiring reports!
)
pause
