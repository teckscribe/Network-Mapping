@echo off
REM ========================================================================
REM  GPON Network Mapping - Install Git Hook (Windows)
REM ========================================================================
set "HOOK_SRC=%~dp0..\.git\hooks\pre-commit"
if exist "%~dp0..\.git\hooks" (
    echo [SUCCESS] Git hooks directory detected.
    echo [INFO] Pre-commit hook is active and will auto-update wiring reports on every commit!
) else (
    echo [ERROR] .git directory not found. Is this a git repository?
)
pause
