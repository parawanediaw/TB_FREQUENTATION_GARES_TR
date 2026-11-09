#!/usr/bin/env bash
# ==========================================================================
#  ÉTAPE 1 (Linux) — à lancer sur une machine Linux QUI A ACCÈS À INTERNET.
#  (Depuis un PC Windows : scripts\1_preparer_sur_un_pc_avec_internet.bat 3.12 linux)
#  Télécharge les paquets Python pour un serveur Linux 64 bits (dossier paquets_linux/),
#  ainsi que la police et la carte (dossier static/).
#  Usage : ./1_preparer_sur_un_pc_avec_internet.sh [version de Python du serveur, ex. 3.12]
# ==========================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/commun.sh"
cd "$RACINE"
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
  read -r -p "Version de Python du SERVEUR (commande « python --version » sur le serveur, ex. 3.12) : " VERSION
fi
VERSION="${VERSION:-3.12}"

echo "[1/2] Paquets Python pour Linux 64 bits, Python $VERSION…"
"$PYTHON" -m pip download -r requirements.txt -d paquets_linux --only-binary=:all: --implementation cp \
  --python-version "$VERSION" --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
  --platform manylinux_2_28_x86_64

echo "[2/2] Police et carte…"
"$PYTHON" outils/telecharger_ressources.py || echo "[ATTENTION] Ressources non téléchargées : les postes les chargeront depuis Internet."

echo
echo "Terminé. Copier TOUT le dossier sur le serveur, puis y lancer : scripts_linux/2_installer_sur_le_serveur.sh"
