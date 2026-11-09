@echo off
setlocal
REM ======================================================================
REM  ETAPE 1 - A lancer sur un PC Windows QUI A ACCES A INTERNET.
REM  Telecharge tout ce qu'il faut pour installer le tableau de bord sur
REM  le serveur sans Internet :
REM    - les paquets Python             -> dossier paquets\
REM    - les polices et la carte        -> dossier static\
REM  Usage : 1_preparer_sur_un_pc_avec_internet.bat [version de Python du serveur] [linux]
REM          exemple : 1_preparer_sur_un_pc_avec_internet.bat 3.12
REM          serveur Linux : 1_preparer_sur_un_pc_avec_internet.bat 3.12 linux
REM ======================================================================
cd /d "%~dp0.."

set "PYVER=%~1"
if "%PYVER%"=="" (
  echo Quelle est la version de Python du SERVEUR ?
  echo ^(sur le serveur : "python --version" dans l'invite Anaconda Prompt, par exemple 3.12.10 : saisir 3.12^)
  set /p "PYVER=Version (Entree = 3.12) : "
)
if "%PYVER%"=="" set "PYVER=3.12"

call "%~dp0..\outils\python_anaconda.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Anaconda de ce PC : %ANACONDA%

echo.
echo [1/2] Telechargement des paquets Python pour le serveur, Python %PYVER% ...
set "CIBLE=%~2"
if /i "%CIBLE%"=="linux" goto paquets_linux
"%PYTHON%" -m pip download -r requirements.txt -d paquets --only-binary=:all: --platform win_amd64 --python-version %PYVER% --implementation cp
goto paquets_fin
:paquets_linux
"%PYTHON%" -m pip download -r requirements.txt -d paquets_linux --only-binary=:all: --implementation cp --python-version %PYVER% --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64
:paquets_fin
if errorlevel 1 (
  echo.
  echo [ERREUR] Le telechargement des paquets a echoue.
  echo          Verifier l'acces Internet. Derriere un proxy d'entreprise, definir avant de relancer :
  echo          set HTTPS_PROXY=http://adresse-du-proxy:port
  pause
  exit /b 1
)

echo.
echo [2/2] Telechargement des polices et de la carte ...
"%PYTHON%" outils\telecharger_ressources.py
if errorlevel 1 echo [ATTENTION] Ressources non telechargees : les postes les chargeront depuis Internet s'ils y ont acces.

echo.
echo ======================================================================
echo  Termine. Copier TOUT le dossier sur le serveur, puis y lancer :
echo  scripts\2_installer_sur_le_serveur.bat   (serveur Windows)
echo  scripts_linux/2_installer_sur_le_serveur.sh   (serveur Linux)
echo ======================================================================
pause
