@echo off
setlocal
REM ======================================================================
REM  ETAPE 2 - Installation sur le serveur (aucun acces Internet requis).
REM  Cree l'environnement Python .venv, installe les paquets du dossier
REM  paquets\, prepare le fichier des identifiants puis verifie tout.
REM ======================================================================
cd /d "%~dp0.."

call "%~dp0..\outils\python_anaconda.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
for /f "tokens=2" %%v in ('"%PYTHON%" --version 2^>^&1') do echo Python %%v d'Anaconda detecte dans %ANACONDA%
REM Memorise le dossier d'Anaconda : le demarrage automatique (compte SYSTEM) ne le trouverait pas seul
findstr /v /b "#" "%FICHIER_ANACONDA%" 2>nul | findstr /r "." >nul || (
  >>"%FICHIER_ANACONDA%" echo %ANACONDA%
)

if not exist paquets\ (
  echo [ERREUR] Dossier "paquets" absent : lancer d'abord l'etape 1 sur un PC avec Internet,
  echo          puis copier tout le dossier sur ce serveur.
  pause
  exit /b 1
)

echo.
echo [1/3] Environnement Python de l'application ^(.venv^) ...
if not exist .venv\Scripts\python.exe (
  "%PYTHON%" -m venv .venv
  if errorlevel 1 (
    echo [ERREUR] Creation de .venv impossible.
    pause
    exit /b 1
  )
)

echo.
echo [2/3] Installation des paquets depuis le dossier paquets ^(sans Internet^) ...
.venv\Scripts\python.exe -m pip install --no-index --find-links=paquets -r requirements.txt
if errorlevel 1 (
  echo.
  echo [ERREUR] Installation impossible. Cause la plus frequente : la version de Python
  echo          choisie a l'etape 1 differe de celle de ce serveur. Relancer l'etape 1
  echo          avec la version affichee plus haut ^(par exemple 3.12^).
  pause
  exit /b 1
)

echo.
echo [3/3] Identifiants SQL Server ...
if not exist .streamlit\secrets.toml (
  copy /y .streamlit\secrets.toml.exemple .streamlit\secrets.toml >nul
  echo Le fichier .streamlit\secrets.toml vient d'etre cree.
  echo Le Bloc-notes s'ouvre : saisir les identifiants, enregistrer, fermer le Bloc-notes.
  notepad .streamlit\secrets.toml
) else (
  echo .streamlit\secrets.toml existe deja : conserve.
)

echo.
echo Verification de la connexion et des requetes SQL ...
echo.
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe verifier_installation.py
echo.
echo ======================================================================
echo  Si la verification signale des problemes : corriger les fichiers du
echo  dossier sql\ ou .streamlit\secrets.toml, puis relancer la verification :
echo      .venv\Scripts\python.exe verifier_installation.py
echo  Ensuite : scripts\3_lancer.bat pour essayer, puis
echo            scripts\4_demarrage_automatique.bat pour la mise en service.
echo ======================================================================
pause
