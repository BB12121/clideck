@echo off
setlocal

cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-clideck-desktop.ps1" %*
set CLI_DECK_EXIT=%ERRORLEVEL%

if not "%CLI_DECK_EXIT%"=="0" (
    echo.
    echo CliDeck Desktop exited with error code %CLI_DECK_EXIT%.
    pause
)

endlocal
exit /b %CLI_DECK_EXIT%
