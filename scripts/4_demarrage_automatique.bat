@echo off
setlocal
REM ======================================================================
REM  ETAPE 4 - Mise en service. A lancer EN TANT QU'ADMINISTRATEUR
REM  (clic droit > Executer en tant qu'administrateur).
REM  - cree la tache planifiee "Frequentation des gares" : le tableau de
REM    bord demarre avec le serveur et repart tout seul s'il s'arrete ;
REM  - ouvre le port 8501 dans le pare-feu Windows ;
REM  - demarre le tableau de bord.
REM ======================================================================
cd /d "%~dp0.."
call "%~dp0..\outils\python_anaconda.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] Droits administrateur requis : clic droit sur ce fichier,
  echo          puis "Executer en tant qu'administrateur".
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo [ERREUR] Installer d'abord : scripts\2_installer_sur_le_serveur.bat
  pause
  exit /b 1
)

echo [1/3] Tache planifiee "Frequentation des gares" ...
.venv\Scripts\python.exe outils\installer_tache.py
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo [2/3] Pare-feu Windows : ouverture du port 8501 ...
netsh advfirewall firewall delete rule name="Frequentation des gares (8501)" >nul 2>&1
netsh advfirewall firewall add rule name="Frequentation des gares (8501)" dir=in action=allow protocol=TCP localport=8501

echo.
echo [3/3] Demarrage ...
schtasks /run /tn "Frequentation des gares"

echo.
echo ======================================================================
echo  Le tableau de bord tourne en arriere-plan et redemarrera avec le serveur.
echo  Adresse : http://%COMPUTERNAME%:8501     Journal : logs\streamlit.log
echo  Pour l'arreter : scripts\5_arreter.bat
echo ======================================================================
pause
