@echo off
REM ======================================================================
REM  Trouve le Python d'Anaconda et prepare son environnement
REM  (equivalent de "conda activate base"). Appele par les autres scripts.
REM  Resultat : variables ANACONDA (dossier) et PYTHON (python.exe).
REM  Ordre de recherche :
REM    1. le dossier ecrit dans outils\chemin_anaconda.txt
REM    2. les emplacements habituels d'Anaconda et de Miniconda
REM ======================================================================
set "ANACONDA="
set "FICHIER_ANACONDA=%~dp0chemin_anaconda.txt"
if exist "%FICHIER_ANACONDA%" for /f "usebackq eol=# tokens=* delims=" %%L in ("%FICHIER_ANACONDA%") do if not defined ANACONDA set "ANACONDA=%%~L"
REM Tolere un chemin saisi un peu a cote : barre finale, sous-dossier Scripts ou fichier python.exe
if defined ANACONDA if /i "%ANACONDA:~-11%"=="\python.exe" set "ANACONDA=%ANACONDA:~0,-11%"
if defined ANACONDA if "%ANACONDA:~-1%"=="\" set "ANACONDA=%ANACONDA:~0,-1%"
if defined ANACONDA if /i "%ANACONDA:~-8%"=="\Scripts" set "ANACONDA=%ANACONDA:~0,-8%"
if defined ANACONDA if not exist "%ANACONDA%\python.exe" (
  echo [ERREUR] python.exe introuvable dans "%ANACONDA%".
  echo          Corriger le dossier ecrit dans outils\chemin_anaconda.txt
  exit /b 1
)
if not defined ANACONDA for %%D in ("%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\anaconda3" "C:\anaconda3" "D:\anaconda3" "E:\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\miniconda3" "C:\miniconda3") do if not defined ANACONDA if exist "%%~D\python.exe" set "ANACONDA=%%~D"
if not defined ANACONDA (
  echo [ERREUR] Anaconda introuvable.
  echo          Ecrire son dossier dans outils\chemin_anaconda.txt
  echo          ^(exemple : C:\ProgramData\anaconda3^), puis relancer.
  exit /b 1
)
set "PATH=%ANACONDA%;%ANACONDA%\Library\mingw-w64\bin;%ANACONDA%\Library\usr\bin;%ANACONDA%\Library\bin;%ANACONDA%\Scripts;%PATH%"
set "PYTHON=%ANACONDA%\python.exe"
exit /b 0
