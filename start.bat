@echo off
title MasterDebater
cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" goto activate
echo First-time setup, this takes a minute or two...
python -m venv .venv
:activate
call ".venv\Scripts\activate.bat"
echo Checking packages...
pip install -q -r requirements.txt

if not exist data mkdir data
if exist "data\apikey.txt" goto havekey
echo.
set /p ANTHROPIC_API_KEY=Paste your Anthropic API key and press Enter: 
>"data\apikey.txt" echo %ANTHROPIC_API_KEY%
echo Saved. You will not be asked again. Delete data\apikey.txt to change it.
goto run
:havekey
set /p ANTHROPIC_API_KEY=<"data\apikey.txt"

:run
echo.
echo Starting MasterDebater. Keep this window open while you use it.
streamlit run app.py
pause
