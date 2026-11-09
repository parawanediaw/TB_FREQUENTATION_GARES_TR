"""
Crée (ou remplace) la tâche planifiée Windows « Frequentation des gares ».

La tâche lance outils\\service.bat au démarrage du serveur, avec le compte SYSTEM, sans limite de durée
(par défaut, une tâche créée en ligne de commande s'arrête au bout de 3 jours : d'où ce fichier XML).
Appelé par scripts\\4_demarrage_automatique.bat (droits administrateur requis).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

NOM = "Frequentation des gares"
DOSSIER = Path(__file__).resolve().parent.parent
SERVICE = DOSSIER / "outils" / "service.bat"

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Tableau de bord Frequentation des gares (Streamlit, port 8501). Dossier : {dossier}</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT1M</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{commande}</Command>
      <WorkingDirectory>{dossier}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def installer_tache() -> int:
    if not SERVICE.exists():
        print(f"[ERREUR] Fichier introuvable : {SERVICE}")
        return 1
    xml = XML.format(commande=escape(str(SERVICE)), dossier=escape(str(DOSSIER)))
    with tempfile.TemporaryDirectory() as tmp:
        fichier = Path(tmp) / "tache.xml"
        fichier.write_text(xml, encoding="utf-16")  # le Planificateur de tâches attend de l'UTF-16
        r = subprocess.run(["schtasks", "/create", "/tn", NOM, "/xml", str(fichier), "/ru", "SYSTEM", "/f"],
                           capture_output=True, text=True)
    sortie = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        print(f"[ERREUR] Création de la tâche planifiée impossible : {sortie}")
        return 1
    print(f"Tâche planifiée « {NOM} » créée : démarrage avec le serveur, compte SYSTEM, sans limite de durée.")
    return 0


if __name__ == "__main__":
    sys.exit(installer_tache())
