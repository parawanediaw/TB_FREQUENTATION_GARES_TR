@echo off
setlocal
REM ======================================================================
REM  Arrete le tableau de bord (tache planifiee et programme sur le port 8501).
REM  Il redemarrera au prochain demarrage du serveur. Pour supprimer la tache :
REM      schtasks /delete /tn "Frequentation des gares" /f
REM  A lancer en tant qu'administrateur.
REM ======================================================================
set PORT=8501
schtasks /end /tn "Frequentation des gares" >nul 2>&1
for /f "tokens=2,3,5" %%a in ('netstat -ano -p tcp') do (
  if "%%a"=="0.0.0.0:%PORT%" if "%%b"=="0.0.0.0:0" taskkill /f /pid %%c >nul 2>&1
)
echo Tableau de bord arrete.
pause
