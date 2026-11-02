@echo off
REM bd-auto launcher - run a technical SEO audit without typing the venv path.
REM Usage:  audit client.com           (pass any args, e.g. audit client.com --no-pdf)
REM Or double-click this file and paste a URL when prompted.

setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" goto nopy
if "%~1"=="" goto prompt

"%PY%" "%~dp0audit.py" %*
goto done

:prompt
set "URL="
set /p "URL=Enter client URL (e.g. https://client.com): "
if not defined URL goto noinput
"%PY%" "%~dp0audit.py" "%URL%"
goto done


   

:nopy
echo [bd-auto] Virtualenv python not found at:
echo     %PY%
echo Run setup first:
echo     py -m venv .venv
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
echo     .venv\Scripts\python.exe -m playwright install chromium
goto done

:noinput
echo No URL entered. Exiting.


:done
echo.
pause
endlocal

