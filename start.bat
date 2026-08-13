@echo off
title Secretary
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
  echo Creating the virtual environment...
  python -m venv venv
  venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
)

if not exist instance\secretary.db (
  echo Creating the database...
  venv\Scripts\python.exe init_db.py
)

echo.
echo   Secretary is starting on http://localhost:5001
echo   The first account created becomes the admin and needs no invite code.
echo   Press Ctrl+C to stop.
echo.

venv\Scripts\python.exe app.py
