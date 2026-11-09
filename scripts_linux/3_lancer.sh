#!/usr/bin/env bash
# ÉTAPE 3 (Linux) — lancement manuel, pour essayer. Ctrl+C arrête le tableau de bord.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/commun.sh"
cd "$RACINE"
[ -x .venv/bin/python ] || { echo "[ERREUR] Installer d'abord : scripts_linux/2_installer_sur_le_serveur.sh"; exit 1; }
if [ "$ADRESSE" = "127.0.0.1" ]; then
  echo "Streamlit écoute sur 127.0.0.1:$PORT (accessible seulement via le proxy nginx : https://NOM_DNS/)"
  echo "Essai depuis le serveur :  curl -s http://127.0.0.1:$PORT/_stcore/health   (doit répondre ok)"
else
  echo "Adresse depuis un poste du réseau : http://$(hostname -f 2>/dev/null || hostname):$PORT"
fi
echo "(Ctrl+C pour arrêter)"
PYTHONIOENCODING=utf-8 exec .venv/bin/python -m streamlit run app.py
