#!/usr/bin/env bash
set -euo pipefail

if [[ -f "$(dirname "$0")/.env" ]]; then
  source "$(dirname "$0")/.env"
fi

: "${SSH_HOST:?SSH_HOST is required}"
: "${SSH_USER:?SSH_USER is required}"
: "${ADDON_DIR:?ADDON_DIR is required}"
: "${PLAYBOOK_DIR:?PLAYBOOK_DIR is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${INVENTORY:?INVENTORY is required}"
: "${PLAYBOOK_FILE:?PLAYBOOK_FILE is required}"
: "${TAGS:?TAGS is required}"

echo "Pulling latest and running Ansible playbook on ${SSH_HOST}..."

ssh -p "${SSH_PORT:-22}" "${SSH_USER}@${SSH_HOST}" bash <<EOF
  set -euo pipefail

  cd ${ADDON_DIR}
  git pull

  source ${SOURCE_DIR}/bin/activate

  cd ${PLAYBOOK_DIR}
  ansible-playbook ${PLAYBOOK_FILE} -i ${INVENTORY} --tags ${TAGS}
EOF

echo "Done."
