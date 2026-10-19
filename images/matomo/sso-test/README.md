# Keycloak login test

Throwaway Matomo (this image) and Keycloak for checking the Keycloak login by
hand. Own Compose project `matomo-sso-test`, so `down` here deletes nothing
else. Not for production: all passwords and the
client secret are fixed test values.

## Requirements

- Docker with Compose v2, run from a host shell (WSL, Git Bash)
- Free local ports 8092 (Keycloak) and 8093 (Matomo)

The browser reaches Keycloak as `http://localhost:8092`, Matomo as
`http://keycloak:8092` (`MATOMO_OIDC_BACKCHANNEL_URL`). Keycloak issues the
tokens for `localhost:8092` either way, so no hosts entry or proxy exception is
needed.

## Use

```bash
images/matomo/sso-test/sso-test.sh up    # build, install Matomo, restart, create the Matomo user anna
images/matomo/sso-test/sso-test.sh logs  # follow Matomo and Keycloak
images/matomo/sso-test/sso-test.sh down  # stop and delete everything, including data
```

| What | Address | Login |
| --- | --- | --- |
| Matomo | http://localhost:8093/ | break-glass `admin` / `admin-Passw0rd!` |
| Keycloak admin console | http://localhost:8092/admin/ | `admin` / `admin` (master realm) |

The realm `dez` comes from [realm-dez.json](realm-dez.json). As in production,
the client `digital-energy-twin` holds the roles `matomoViewer` and
`matomoAdmin` (`matomoAdmin` includes `matomoViewer`). The `matomo` client uses
its own browser flow `matomo browser`: after the login, or with an existing
Keycloak session, the subflow `matomo role gate` denies users who have neither
role. The users log in with their username as password:

| Keycloak user | Role | Matomo user | Purpose |
| --- | --- | --- | --- |
| `anna` | `matomoViewer` | `anna`, view access to site 1 | Normal Keycloak login |
| `ben` | `matomoViewer` | none | Rejected by Matomo |
| `carla` | none | `carla`, view access to site 1 | Rejected by Keycloak |
| `admin` | `matomoAdmin` | `admin` is the break-glass superuser | Must not be able to take over the superuser |

Use a private browser window per test, so no session is left over.

## Checks

| # | Check | Expected |
| --- | --- | --- |
| B1 | `up` output after the restart | No "not installed yet" message; LoginOIDC is active |
| B2 | Matomo login page | A **Keycloak** button next to the password form; under *Administration > System > General settings > LoginOIDC* the settings are read-only |
| B3 | Keycloak button, log in as `anna` | Back in Matomo as `anna`, site 1 visible |
| B4 | Keycloak button, log in as `ben` | Matomo shows an error, no user `ben` appears under *Administration > System > Users* |
| B5 | As `anna`: log out in Matomo, then click the Keycloak button again | Logout ends on Keycloak's "logged out" page (no redirect back, Matomo sends no `post_logout_redirect_uri`); the next login asks for the password again |
| B6 | Password form, `admin` / `admin-Passw0rd!` | Works |
| B7 | Keycloak button, log in as Keycloak user `admin` | Matomo shows "OAuth login disabled for superusers." and you are not logged in |
| B8 | `MATOMO_OIDC_CLIENT_ID= docker compose -f images/matomo/sso-test/compose.yml up -d matomo`, then `logs matomo` | Container stops with "MATOMO_OIDC_CLIENT_ID: is required with MATOMO_OIDC_ISSUER". Restore with the same command without the variable |
| B9 | Keycloak button, log in as `carla` | Keycloak shows "Kein Zugriff auf Matomo: …" and does not return to Matomo. If `carla` gets in, a role name in the flow is wrong: the Keycloak log then says "Invalid role name submitted" |
| B10 | Log in as `carla` at http://localhost:8092/realms/dez/account first, then click the Keycloak button in Matomo | Denied as in B9, although a Keycloak session exists |
| B11 | Keycloak admin console, realm `dez`: *Users > anna > Role mapping*, unassign `matomoViewer`; then log in as `anna` in a new private window | Denied as in B9. Assign the role again afterwards |
| B12 | Password form, `carla` with any password | Fails: `anna` and `carla` got a random password nobody knows |
| B13 | Login page, *Lost your password?*, enter `carla` and a new password | Matomo shows "Setting a Matomo password is disabled. Please log in via Keycloak." (plugin NoPasswordReset) |
| B14 | As `admin` (password form): *Administration > System > Users > Invite user*, invite `dora`, copy the invitation link, open it in a new private window | The same message as B13; `dora` cannot set a password |
