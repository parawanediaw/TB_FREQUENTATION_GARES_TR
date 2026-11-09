@echo off
REM ======================================================================
REM  Lance le tableau de bord et le relance s'il s'arrete (utilise par la
REM  tache planifiee). Journal : logs\streamlit.log (10 Mo au maximum).
REM ======================================================================
cd /d "%~dp0.."
if not exist logs mkdir logs
call "%~dp0python_anaconda.bat" >> "%~dp0..\logs\streamlit.log" 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
:boucle
if exist logs\streamlit.log for %%F in (logs\streamlit.log) do if %%~zF GTR 10000000 move /y logs\streamlit.log logs\streamlit.ancien.log >nul
echo [%date% %time%] Demarrage du tableau de bord >> logs\streamlit.log
".venv\Scripts\python.exe" -m streamlit run app.py >> logs\streamlit.log 2>&1
echo [%date% %time%] Arret (code %errorlevel%), redemarrage dans 15 secondes >> logs\streamlit.log
ping -n 16 127.0.0.1 >nul
goto boucle
