@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 scripts\launch.py %*
) else (
    python scripts\launch.py %*
)
set "ATELIER_CODE=%errorlevel%"
if not "%ATELIER_CODE%"=="0" if not "%ATELIER_CODE%"=="130" pause
exit /b %ATELIER_CODE%
