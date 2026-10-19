#!/usr/bin/env bash
# Throwaway Matomo and Keycloak for testing the Keycloak login. See README.md.
set -euo pipefail
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1 # Git Bash: keep container paths as they are

MATOMO=http://localhost:8093
ADMIN_PASSWORD='admin-Passw0rd!'
compose() { docker compose -f compose.yml "$@"; }
api() {
  curl -fsS "$MATOMO/index.php" --data module=API --data format=json "$@"
}

case "${1:-}" in
  up)
    compose up -d --build --wait keycloak matomo
    echo 'Installing Matomo (skipped if already installed) ...'
    compose run --build --rm playwright install.spec.ts
    echo 'Restarting Matomo so the entrypoint activates LoginOIDC ...'
    compose restart matomo
    compose up -d --wait matomo
    compose logs matomo | grep -iE 'loginoidc|nopasswordreset|activat|error' || true

    echo 'Creating the Matomo users anna and carla (ben stays without one) ...'
    token=$(api --data method=UsersManager.createAppSpecificTokenAuth \
      --data userLogin=admin --data-urlencode "passwordConfirmation=$ADMIN_PASSWORD" \
      --data description=sso-test | sed -E 's/.*"value":"([^"]+)".*/\1/')
    # As in production: a random password nobody keeps, so the password form
    # is no way around Keycloak.
    for login in anna carla; do
      if api --data method=UsersManager.userExists --data "userLogin=$login" \
        --data "token_auth=$token" | grep -q true; then
        echo "$login already exists"
      else
        api --data method=UsersManager.addUser --data "userLogin=$login" \
          --data "password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')" \
          --data "email=$login@example.invalid" \
          --data initialIdSite=1 --data-urlencode "passwordConfirmation=$ADMIN_PASSWORD" \
          --data "token_auth=$token"
        echo
      fi
    done
    cat << EOF

Matomo:    $MATOMO/  (break-glass superuser admin / $ADMIN_PASSWORD)
Keycloak:  http://localhost:8092/admin/  (admin / admin, realm dez)
Keycloak users (password = username), see README.md: anna (role + Matomo user),
ben (role, no Matomo user), carla (Matomo user, no role), admin (role, same name
as the Matomo superuser).
EOF
    ;;
  logs) compose logs -f matomo keycloak ;;
  down) compose --profile tools down -v ;;
  *)
    echo "usage: $0 up|logs|down" >&2
    exit 1
    ;;
esac
