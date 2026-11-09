#!/usr/bin/env bash
# ==========================================================================
#  ÉTAPE 2 (Linux) — installation sur le serveur, sans Internet.
#  Crée l'environnement .venv, installe les paquets de paquets_linux/,
#  prépare le fichier des identifiants, puis vérifie la connexion et les requêtes.
# ==========================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/commun.sh"
cd "$RACINE"
echo "Anaconda : $ANACONDA ($("$PYTHON" --version 2>&1))"
# Mémorise le dossier d'Anaconda pour le démarrage automatique (service systemd)
if ! (grep -v '^[[:space:]]*#' "$FICHIER_ANACONDA" 2>/dev/null | grep -q .); then
  echo "$ANACONDA" >> "$FICHIER_ANACONDA"
fi

if [ ! -d paquets_linux ]; then
  echo "[ERREUR] Dossier paquets_linux absent : lancer d'abord l'étape 1 sur une machine qui a Internet."
  exit 1
fi
if ! command -v odbcinst >/dev/null 2>&1 && [ ! -e /etc/odbcinst.ini ]; then
  echo "[ATTENTION] unixODBC et le pilote Microsoft ODBC pour SQL Server ne semblent pas installés."
  echo "            Les faire installer (paquets msodbcsql18 et unixODBC) : voir GUIDE_DEPLOIEMENT.html."
fi

echo "[1/3] Environnement Python de l'application (.venv)…"
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv

echo "[2/3] Installation des paquets depuis paquets_linux (sans Internet)…"
if ! .venv/bin/python -m pip install --no-index --find-links=paquets_linux -r requirements.txt; then
  echo "[ERREUR] Installation impossible. Cause fréquente : la version de Python choisie à l'étape 1"
  echo "         diffère de celle du serveur ($("$PYTHON" --version 2>&1)). Relancer l'étape 1 avec la bonne version."
  exit 1
fi

echo "[3/3] Identifiants SQL Server…"
if [ ! -f .streamlit/secrets.toml ]; then
  cp .streamlit/secrets.toml.exemple .streamlit/secrets.toml
  chmod 600 .streamlit/secrets.toml
  echo "Le fichier .streamlit/secrets.toml vient d'être créé (lisible par vous seul)."
  echo "Le remplir, par exemple :  nano .streamlit/secrets.toml   puis relancer ce script."
  exit 0
fi

echo
echo "Vérification de la connexion et des requêtes SQL…"
PYTHONIOENCODING=utf-8 .venv/bin/python verifier_installation.py || true
echo
echo "Ensuite : scripts_linux/3_lancer.sh pour essayer, puis sudo scripts_linux/4_demarrage_automatique.sh"
