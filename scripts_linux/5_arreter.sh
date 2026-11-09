#!/usr/bin/env bash
# Arrête le tableau de bord (il redémarrera avec le serveur).
# Pour ne plus le démarrer automatiquement :  sudo systemctl disable frequentation-gares
set -euo pipefail
sudo systemctl stop frequentation-gares.service
echo "Tableau de bord arrêté."
