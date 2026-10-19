#!/bin/bash
# Prepares Matomo on every start, then hands over to the official entrypoint:
# copies the plugins (LoginOIDC, NoPasswordReset) and the config into the
# volume, adds the Keycloak settings from the environment and activates the
# plugins once Matomo is installed.
set -euo pipefail
cd /var/www/html

# Let the official entrypoint fill an empty volume first.
/entrypoint.sh true

mkdir -p config plugins
config=config/common.config.ini.php
cp /opt/matomo-config/common.config.ini.php "$config"
plugins=(LoginOIDC NoPasswordReset)
for plugin in "${plugins[@]}"; do
  rm -rf "plugins/$plugin"
  cp -r "/opt/matomo-plugins/$plugin" "plugins/$plugin"
done

# Values end up in double-quoted INI strings, where PHP would interpret these.
check_ini_value() {
  case "$2" in
    *'"'* | *$'\n'* | *$'\r'* | *'$'* | *'\'*)
      echo >&2 "error: $1 must not contain quotes, line breaks, \$ or backslashes"; exit 1 ;;
  esac
}

general=
if [ "${MATOMO_DISABLE_TRUSTED_HOST_CHECK:-}" = 1 ]; then general+=$'enable_trusted_host_check = 0\n'; fi
if [ "${MATOMO_BEHIND_TLS_PROXY:-}" = 1 ]; then general+=$'assume_secure_protocol = 1\n'; fi
if [ -n "$general" ]; then printf '\n[General]\n%s' "$general" >> "$config"; fi

oidc_enabled=
if [ -n "${MATOMO_OIDC_ISSUER:-}" ]; then
  oidc_enabled=1
  : "${MATOMO_OIDC_CLIENT_ID:?is required with MATOMO_OIDC_ISSUER}"
  : "${MATOMO_OIDC_CLIENT_SECRET_FILE:?is required with MATOMO_OIDC_ISSUER}"
  endpoint=${MATOMO_OIDC_ISSUER%/}/protocol/openid-connect
  # Token and userinfo are called by Matomo itself, e.g. via an internal URL.
  backchannel=${MATOMO_OIDC_BACKCHANNEL_URL:-$MATOMO_OIDC_ISSUER}
  backchannel=${backchannel%/}/protocol/openid-connect
  # Secret files written on Windows end in \r\n.
  client_secret=$(tr -d '\r' < "$MATOMO_OIDC_CLIENT_SECRET_FILE")
  userinfo_id=${MATOMO_OIDC_USERINFO_ID:-preferred_username}
  check_ini_value MATOMO_OIDC_ISSUER "$endpoint"
  check_ini_value MATOMO_OIDC_BACKCHANNEL_URL "$backchannel"
  check_ini_value MATOMO_OIDC_CLIENT_ID "$MATOMO_OIDC_CLIENT_ID"
  check_ini_value MATOMO_OIDC_CLIENT_SECRET_FILE "$client_secret"
  check_ini_value MATOMO_OIDC_USERINFO_ID "$userinfo_id"
  cat >> "$config" <<EOF

[LoginOIDC]
authenticationName = "Keycloak"
authorizeUrl = "$endpoint/auth"
tokenUrl = "$backchannel/token"
userinfoUrl = "$backchannel/userinfo"
endSessionUrl = "$endpoint/logout"
clientId = "$MATOMO_OIDC_CLIENT_ID"
clientSecret = "$client_secret"
scope = "openid email profile"
userinfoId = "$userinfo_id"
allowSignup = 0
autoLinking = 1
disableSuperuser = 1
disableDirectLoginUrl = 1
bypassTwoFa = 0
EOF
fi

for plugin in "${plugins[@]}"; do chown -R www-data:www-data "plugins/$plugin"; done
chown -R www-data:www-data config
# Holds the client secret.
chmod 600 "$config"

# Activation needs a finished installation; the first start skips it.
if [ -n "$oidc_enabled" ]; then
  if grep -q '^\[database\]' config/config.ini.php 2>/dev/null \
    && ! grep -q '^installation_in_progress' config/config.ini.php; then
    # Without NoPasswordReset users could set a password and skip Keycloak,
    # so a failed activation stops the start instead of running unprotected.
    su -s /bin/bash www-data -c "./console plugin:activate ${plugins[*]}" \
      || { echo >&2 "error: could not activate ${plugins[*]}"; exit 1; }
  else
    echo >&2 'LoginOIDC: Matomo is not installed yet; restart after the installation to activate it'
  fi
fi

exec /entrypoint.sh "$@"
