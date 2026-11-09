#!/usr/bin/env bash
# ==========================================================================
#  ÉTAPE 4 (Linux) — mise en service. À lancer avec sudo :
#      sudo scripts_linux/4_demarrage_automatique.sh
#  - crée le service systemd « frequentation-gares » : le tableau de bord démarre
#    avec le serveur et repart tout seul s'il s'arrête ;
#  - pare-feu : derrière nginx (address = "127.0.0.1"), le port de Streamlit reste FERMÉ ;
#    sans proxy, il est ouvert (firewalld ou ufw, s'il y en a un) ;
#  - démarre le tableau de bord.
#  Le service tourne avec le compte qui a lancé sudo (pas avec root).
# ==========================================================================
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/commun.sh"
cd "$RACINE"
[ "$(id -u)" -eq 0 ] || { echo "[ERREUR] Lancer avec sudo : sudo $0"; exit 1; }
[ -x .venv/bin/python ] || { echo "[ERREUR] Installer d'abord : scripts_linux/2_installer_sur_le_serveur.sh"; exit 1; }
COMPTE="${SUDO_USER:-$(stat -c %U "$RACINE")}"
UNITE=/etc/systemd/system/frequentation-gares.service

cat > "$UNITE" <<UNIT
[Unit]
Description=Tableau de bord Frequentation des gares (Streamlit, port $PORT)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$COMPTE
WorkingDirectory=$RACINE
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$ANACONDA/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$RACINE/.venv/bin/python -m streamlit run app.py
Restart=always
RestartSec=15
# Durcissement : pas d'élévation de droits, /usr /boot /etc en lecture seule, /tmp privé
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now frequentation-gares.service

# Identifiants lisibles par le seul compte du service
[ -f .streamlit/secrets.toml ] && chown "$COMPTE" .streamlit/secrets.toml && chmod 600 .streamlit/secrets.toml

if [ "$ADRESSE" = "127.0.0.1" ]; then
  # Derrière nginx : le port de Streamlit ne doit pas être joignable depuis le réseau
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --remove-port=$PORT/tcp >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-service=http --add-service=https && firewall-cmd --reload
  elif command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
    ufw delete allow $PORT/tcp >/dev/null 2>&1 || true
    ufw allow 80/tcp && ufw allow 443/tcp
  fi
else
  if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port=$PORT/tcp && firewall-cmd --reload
  elif command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
    ufw allow $PORT/tcp
  fi
fi

echo
echo "Service « frequentation-gares » actif (compte $COMPTE)."
if [ "$ADRESSE" = "127.0.0.1" ]; then
  echo "Streamlit écoute sur 127.0.0.1:$PORT ; accès des utilisateurs : https://NOM_DNS/ (proxy nginx, voir securite/)"
else
  echo "Adresse : http://$(hostname -f 2>/dev/null || hostname):$PORT"
fi
echo "Journal : journalctl -u frequentation-gares -f      Arrêt : sudo scripts_linux/5_arreter.sh"
