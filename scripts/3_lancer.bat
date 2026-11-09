@echo off
setlocal
REM ======================================================================
REM  ETAPE 3 - Lancement manuel, pour essayer. Fermer la fenetre arrete
REM  le tableau de bord. Pour la mise en service : etape 4.
REM ======================================================================
cd /d "%~dp0.."
call "%~dp0..\outils\python_anaconda.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo [ERREUR] Installer d'abord : scripts\2_installer_sur_le_serveur.bat
  pause
  exit /b 1
)
echo Le tableau de bord demarre.
echo Adresse depuis un poste du reseau : http://%COMPUTERNAME%:8501
echo ^(sur ce serveur : http://localhost:8501^)
echo Fermer cette fenetre arrete le tableau de bord.
echo.
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m streamlit run app.py
pause
