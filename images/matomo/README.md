# Matomo image

The official `matomo:5.14.0-apache` image plus the
[LoginOIDC](https://github.com/dominik-th/matomo-plugin-LoginOIDC) plugin
(5.0.0), so Matomo users log in via the platform Keycloak, and the small
[NoPasswordReset](plugins/NoPasswordReset/NoPasswordReset.php) plugin. On every
start the entrypoint copies the plugins and
[common.config.ini.php](common.config.ini.php) into the volume, writes the
Keycloak settings from the environment and activates the plugins once Matomo is
installed. Settings from the config file are read-only in the Matomo UI.

The password form stays on the login page; LoginOIDC cannot remove it. So that
it is no way around Keycloak and its role check, normal users get a random
password nobody keeps, and NoPasswordReset blocks *Lost your password?* and
invitation links while LoginOIDC is configured (also once Matomo can send mail
for e-mail reports).

Without `MATOMO_OIDC_ISSUER` the image behaves like the official one (password
login only).

The addon deploys this image and does the Keycloak and installation steps below
itself; see "Matomo web analytics" in the [addon README](../../README.md). This
file describes the image for anyone running it on their own.

## Environment

| Variable | Required | Meaning |
| --- | --- | --- |
| `MATOMO_DATABASE_*` | yes | As in the official image. |
| `MATOMO_OIDC_ISSUER` | for SSO | Realm URL, e.g. `https://keycloak.example/realms/dez`. |
| `MATOMO_OIDC_CLIENT_ID` | for SSO | Keycloak client ID, e.g. `matomo`. |
| `MATOMO_OIDC_CLIENT_SECRET_FILE` | for SSO | Path to a mounted file with the client secret. |
| `MATOMO_OIDC_BACKCHANNEL_URL` | no | Realm URL Matomo itself uses for the token and userinfo calls, e.g. the internal Keycloak service. Default: `MATOMO_OIDC_ISSUER`. Keycloak must still issue tokens for `MATOMO_OIDC_ISSUER` (its configured hostname). |
| `MATOMO_OIDC_USERINFO_ID` | no | Claim used as the Matomo login. Default `preferred_username`. |
| `MATOMO_BEHIND_TLS_PROXY` | no | `1` when TLS ends at a proxy, so Matomo builds `https` URLs. |
| `MATOMO_DISABLE_TRUSTED_HOST_CHECK` | no | `1` for local development only. |

The fixed settings are: no self-signup (`allowSignup = 0`), and a Keycloak user
is linked to the Matomo user whose login equals their `preferred_username`
(`autoLinking = 1`). Superusers cannot log in via Keycloak
(`disableSuperuser = 1`), so a Keycloak account that happens to share a
superuser's login cannot take it over. The break-glass superuser keeps password
login; anyone else who needs superuser access logs in with a password too.

## Keycloak client

- Confidential OpenID Connect client with the standard flow.
- Valid redirect URI:
  `https://<matomo-host>/index.php?module=LoginOIDC&action=callback&provider=oidc`
- Valid post logout redirect URI: `https://<matomo-host>/*`
- Client scopes `email` and `profile`, so userinfo returns `email` and
  `preferred_username`.
- The realm must not let users change their username ("Edit username" off);
  otherwise a renamed account could take over another Matomo login.
- Matomo must reach the realm's token and userinfo endpoints directly, at
  `MATOMO_OIDC_ISSUER` or `MATOMO_OIDC_BACKCHANNEL_URL`.
- Only users with the client role `matomoViewer` or `matomoAdmin` of the DEZ
  client (`digital-energy-twin`) may log in. LoginOIDC cannot read roles, so
  Keycloak enforces this: the `matomo` client gets its own browser flow (client
  *Advanced > Authentication flow overrides*) that denies users without either
  role, also when they already have a Keycloak session. Removing the role
  blocks the next Matomo login. The flow `matomo browser` in
  [sso-test/realm-dez.json](sso-test/realm-dez.json) is the reference.
  A misspelled role name lets everyone in (Keycloak only logs "Invalid role
  name submitted"), so after setting it up check that a user without either
  role is denied.

## First deployment

1. Start Matomo with the variables above and install it once (web installer,
   or headless with
   [install.spec.ts](sso-test/installer/install.spec.ts)). The
   superuser created here is the break-glass account: give it a login that is
   never a Keycloak username (e.g. `matomo-breakglass`), keep its password in
   the secret store and enable two-factor authentication. It logs in with its
   password only.
2. Restart the container. The log says `Activated plugin LoginOIDC`, and the
   login page shows **Keycloak**.
3. For each person with `matomoViewer` or `matomoAdmin`, create a Matomo user
   whose login is their Keycloak username, with a random password that is not
   kept, and grant the matching access to the DEZ site (*View* or *Admin*).
   Create them via the API, not with the UI's *Invite user*: invitation links
   are blocked, because the invitee would set a password of their own. As the
   break-glass superuser, with an API token from *Personal > Security*:

   ```sh
   curl -fsS https://<matomo-host>/index.php -d module=API -d format=json \
     -d method=UsersManager.addUser -d userLogin=<keycloak-username> \
     -d password="$(openssl rand -hex 24)" -d email=<email> -d initialIdSite=<site-id> \
     -d token_auth=<token> --data-urlencode passwordConfirmation=<break-glass password>
   ```

   `initialIdSite` grants *View*; for *Admin*, call
   `UsersManager.setUserAccess` with `access=admin`, `idSites=<site-id>` and the
   same token and password confirmation afterwards.
   Their first Keycloak login links the accounts.
   Keycloak users without a Matomo user are rejected. When a role is removed,
   also delete or downgrade the Matomo user: an open Matomo session lasts until
   it expires.

## Testing the Keycloak login

[sso-test/](sso-test/README.md) starts this image next to a throwaway Keycloak
with test users and lists the checks.

## Build and release

The workflow *Build Matomo image* (`.github/workflows/matomo-image.yml`, started
by hand on `main`) builds this directory as
`ghcr.io/csi-foxbyte/regensburg-web-matomo`, with the Node and Matomo base
images pinned by digest. It then pins the new digest in
`vars/matomo_image.json`, writes the image SBOM to `sboms/matomo.cdx.json` and
commits both to `main`. The next playbook run deploys that digest. Only changes
in this directory need a new build; the app release no longer contains Matomo.

Build it locally:

```bash
docker build -t dez-matomo:local images/matomo
```
