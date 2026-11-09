#!/usr/bin/env bash
# ==========================================================================
#  Trouve le Python d'Anaconda (inclus par les autres scripts).
#  Ordre de recherche :
#    1. la variable ANACONDA, si elle est définie (ex. ANACONDA=/opt/anaconda3)
#    2. le dossier écrit dans outils/chemin_anaconda_linux.txt
#    3. les emplacements habituels (~/anaconda3, /opt/anaconda3, ~/miniconda3…)
#  Résultat : variables RACINE (dossier de l'application), ANACONDA et PYTHON.
# ==========================================================================
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FICHIER_ANACONDA="$RACINE/outils/chemin_anaconda_linux.txt"

if [ -z "${ANACONDA:-}" ] && [ -f "$FICHIER_ANACONDA" ]; then
  ANACONDA="$(grep -v '^[[:space:]]*#' "$FICHIER_ANACONDA" | grep -m1 . | tr -d '\r' | xargs || true)"
fi
if [ -z "${ANACONDA:-}" ]; then
  for d in "$HOME/anaconda3" "/opt/anaconda3" "/usr/local/anaconda3" "$HOME/miniconda3" "/opt/miniconda3" "/opt/conda"; do
    if [ -x "$d/bin/python" ]; then ANACONDA="$d"; break; fi
  done
fi
if [ -z "${ANACONDA:-}" ] || [ ! -x "$ANACONDA/bin/python" ]; then
  echo "[ERREUR] Anaconda introuvable${ANACONDA:+ dans $ANACONDA}."
  echo "         Écrire son dossier (celui qui contient bin/python) dans outils/chemin_anaconda_linux.txt,"
  echo "         par exemple /opt/anaconda3, puis relancer."
  exit 1
fi
PYTHON="$ANACONDA/bin/python"
export PATH="$ANACONDA/bin:$PATH"

# Port et adresse d'écoute de Streamlit, lus dans .streamlit/config.toml
PORT="$(grep -m1 -E '^[[:space:]]*port[[:space:]]*=' "$RACINE/.streamlit/config.toml" 2>/dev/null | sed -E 's/^[^=]*=[[:space:]]*([0-9]+).*/\1/')"
PORT="${PORT:-8501}"
ADRESSE="$(grep -m1 -E '^[[:space:]]*address[[:space:]]*=' "$RACINE/.streamlit/config.toml" 2>/dev/null | sed -E 's/^[^=]*=[[:space:]]*"([^"]*)".*/\1/')"
ADRESSE="${ADRESSE:-0.0.0.0}"
