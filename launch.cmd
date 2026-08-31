@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_EXE="

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%I"
    )
)

if not defined PYTHON_EXE (
    set "PYTHON_EXE=C:\Users\Tyduc\AppData\Local\Programs\Python\Python313\python.exe"
)

"%PYTHON_EXE%" "%~dp0HSDB.py"
endlocal