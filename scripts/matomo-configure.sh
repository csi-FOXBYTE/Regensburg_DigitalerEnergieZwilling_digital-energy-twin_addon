#!/usr/bin/env bash
# Applies the DEZ privacy settings to a Matomo instance and creates Matomo users
# for Keycloak logins. Asks for the break-glass password; changes nothing if it
# is wrong. See "Matomo web analytics" in README.md.
#
# Usage: scripts/matomo-configure.sh https://matomo.det.example.com [login[:admin] ...]
#   login        Keycloak username (the email address in realms that use it as
#                username); gets View on the DEZ site. login:admin gets Admin.
#   Without logins only the privacy settings are applied.
# A platform CA that curl does not know: CURL_CA_BUNDLE=/path/to/ca.crt scripts/...
set -uo pipefail

if [ $# -lt 1 ] || [[ "$1" != http*://* ]]; then
  sed -n '2,10p' "$0" >&2
  exit 2
fi
M=${1%/}/index.php
shift
SITE_ID=${SITE_ID:-1}
BREAKGLASS=${BREAKGLASS:-matomo-breakglass}

# Pasting the command often leaves a line break in the input, which would be
# read as an empty password.
while read -r -t 0.3 _pending; do :; done
read -rsp "Break-glass password ($BREAKGLASS): " PW; echo
[ -n "$PW" ] || { echo 'No password entered.' >&2; exit 1; }
echo "(${#PW} characters entered)"

created=$(curl -sS "$M" -d module=API -d format=json -d method=UsersManager.createAppSpecificTokenAuth \
  -d "userLogin=$BREAKGLASS" --data-urlencode "passwordConfirmation=$PW" -d description=matomo-configure)
TOKEN=$(printf '%s' "$created" | sed -nE 's/.*"value":"([0-9a-f]+)".*/\1/p')
[ -n "$TOKEN" ] || { echo "Could not create an API token: $created" >&2; exit 1; }

failures=0
api() {
  local answer
  answer=$(curl -sS "$M" -d module=API -d format=json -d "token_auth=$TOKEN" \
    --data-urlencode "passwordConfirmation=$PW" "$@")
  printf '%-45s %s\n' "${2#method=}" "$answer"
  case "$answer" in *'"result":"error"'* | '') failures=$((failures + 1)) ;; esac
}

echo '--- Privacy settings (as described in section 12 of the privacy notice)'
api -d method=PrivacyManager.setAnonymizeIpSettings -d anonymizeIPEnable=1 -d ipAddressMaskLength=4 \
  -d useAnonymizedIpForVisitEnrichment=1 -d anonymizeUserId=1 -d anonymizeOrderId=1 \
  -d anonymizeReferrer=exclude_all -d forceCookielessTracking=0 -d randomizeConfigId=0
api -d method=UserCountry.setLocationProvider -d providerId=disabled
api -d method=PrivacyManager.setComplianceStatus -d "idSite=$SITE_ID" -d complianceType=cnil_v1 -d enforce=1

if [ $# -gt 0 ]; then
  echo '--- Matomo users (login = Keycloak username, random password nobody keeps)'
fi
for entry in "$@"; do
  login=${entry%:admin}
  access=view
  [ "$login" != "$entry" ] && access=admin
  exists=$(curl -sS "$M" -d module=API -d format=json -d method=UsersManager.userExists \
    -d "userLogin=$login" -d "token_auth=$TOKEN")
  if [ "$exists" = '{"value":true}' ]; then
    echo "$login already exists"
  else
    # Matomo requires an email address; logins that are none get a placeholder.
    email=$login
    [[ "$login" == *@* ]] || email="$login@example.invalid"
    api -d method=UsersManager.addUser -d "userLogin=$login" -d "email=$email" \
      --data-urlencode "password=$(openssl rand -base64 36)"
  fi
  api -d method=UsersManager.setUserAccess -d "userLogin=$login" -d "access=$access" -d "idSites=$SITE_ID"
done

echo "--- ${failures} call(s) failed"
[ "$failures" -eq 0 ]
