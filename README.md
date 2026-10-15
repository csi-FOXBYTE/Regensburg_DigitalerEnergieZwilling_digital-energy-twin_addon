# Digital Energy Twin Addon

This addon deploys the Digital Energy Twin stack into Civitas Core:

- Public frontend container
- Admin frontend container
- Backend container
- Optional: Matomo web analytics with login via the platform Keycloak, plus its
  MariaDB and nightly backups (see [Matomo web analytics](#matomo-web-analytics))

The platform's selected Ingress or Gateway API controller routes incoming traffic
to APISIX using dedicated hosts. APISIX applies authentication and API policies before forwarding
requests to the applications.

## Installation

Place this addon in the Civitas Core addons folder and import it via `inv_addons`.

## Configuration

Main config lives under `inv_addons.digital-energy-twin`.
Defaults are defined in [default_inventory.yml](default_inventory.yml).

Minimal example:

```yaml
inv_addons:
  import: true
  addons:
    - "addons/digital-energy-twin_addon/tasks.yml"
  digital-energy-twin:
    enable: true
    ns_create: true
    ns_name: "{{ ENVIRONMENT }}-digitalenergytwin"
    ns_kubeconfig: "{{ kubeconfig_file }}"
    oidc_client_id: "digital-energy-twin"
    admin_host: "admin.det.{{ DOMAIN }}"
    public_host: "det.{{ DOMAIN }}"
    backend_host: "api.det.{{ DOMAIN }}"
    backend_cors_additional_origins: []
    tiles_url: "https://tiles.example.com"
    terrain_url: "https://terrain.example.com"
    address_database_url: "https://tiles.example.com/det-rg-addresses.sqlite"
```

By default, the addon works without any `software` section in your deployment inventory.
The current checked-in selection remains in
[vars/software_references.yml](vars/software_references.yml) until the monorepo's
v1.7.3 release is published and adopted. The **Release addon from application
release** workflow then replaces that file with `vars/application_release.json`,
containing the exact validated [monorepo release manifest](https://github.com/csi-FOXBYTE/Regensburg_DigitalerEnergieZwilling_MonoRepo).
Deployment reads all `image@sha256:...` references from that one manifest and
sets `APP_VERSION` on both static frontends from its `appVersion`. Releases up to
v1.7.3 contain no `matomo` image; with such a release the addon skips Matomo and
says so in the playbook output.

Before deploying, choose the [database mode](#database), check
[platform routing](#platform-routing), and prepare [S3 CORS](#s3-cors-for-map-data).

## Execute

Run the normal Civitas Core playbook. To run addon tasks explicitly, use one of
these tags:

- `addons` for all configured addons
- `addon_digital-energy-twin` for this addon only

The optional [deploy script](#deploy-script) runs the playbook through your Ansible host.

## Admin frontend access

The public frontend at `https://{{ public_host }}` is open without login.
The admin frontend at `https://{{ admin_host }}` requires a Keycloak account
with at least one of the addon client roles below. Replace these placeholders
with the hostnames from your inventory.

**A Keycloak administrator must grant users access by assigning the required
client role**, either directly or through a group. The addon creates `manager`,
`maintainer`, and `admin` on the client configured by `oidc_client_id` (default:
`digital-energy-twin`), but does not assign these roles to users or groups.
Having a Keycloak account alone does not grant admin frontend access.

| Client role | Visible admin frontend areas | Granted access |
| --- | --- | --- |
| `manager` | Dashboard, Gebäudeliste (building list) | View and manage submissions, including assignment, review, approval, rejection, and deletion. No configuration or feedback administration. |
| `maintainer` | Systempflege (system maintenance) | Read, create, activate, and delete calculation configurations; read and delete feedback through the admin API. No submission administration. |
| `admin` | Dashboard, Gebäudeliste, Systempflege | All submission, configuration, and feedback permissions listed above. |

Permissions from multiple roles combine. These roles grant access within the
Digital Energy Twin; the addon `admin` role does not grant Keycloak
administration rights. The backend enforces permissions on individual API
operations as well as the frontend restricting visible areas.

### Grant access in Keycloak

1. Sign in to the Keycloak administration console with an account permitted to
   manage user role assignments. Select the existing platform realm configured
   by `inv_access.tenant.realm_name`.
2. Open **Users** and select the intended user. If necessary, create their
   account and configure login credentials according to your platform's normal
   onboarding process.
3. Open **Role mapping** → **Assign role**, filter by client roles, and select
   the addon client (`digital-energy-twin` by default).
4. Choose `manager`, `maintainer`, or `admin` according to the table, then click
   **Assign**. Use the roles belonging to this client; a realm role or an
   Airflow client role with a similar name does not grant addon access.
5. Ask the user to sign out and sign in again at the admin frontend URL so that
   their session contains the new roles. Verify that the expected areas appear.

For group-based access, assign the same client roles to a Keycloak group and
add the users to that group. See Keycloak's [user role mappings documentation](https://www.keycloak.org/docs/latest/server_admin/#user-role-mappings).

If login succeeds but access is denied or areas are missing, check the realm,
client, effective role assignments, and that the user has signed in again.
To remove access, remove the direct role assignments and any group memberships
that grant them; existing sessions may retain roles until their tokens are
renewed or the sessions are revoked by an administrator.

## S3 CORS for map data

The public frontend requests `/api/public/map-resources` from the backend, then
downloads data **directly from the returned URLs**: `tiles_url` supplies
`tileset.json` and its referenced 3D tiles, `terrain_url` supplies `layer.json`
and its terrain tiles, and `address_database_url` supplies the SQLite database
for address search. The backend does not proxy these downloads.

For storage on another origin, configure CORS on every S3/S3-compatible bucket
serving those files. The addon does not configure bucket CORS, and
`backend_cors_additional_origins` only affects backend API routes.

In the AWS S3 console, open the bucket's **Permissions → Cross-origin resource
sharing (CORS)** settings and merge this rule with any existing rules. Use the
equivalent bucket CORS settings/API for other S3-compatible services:

```json
[
  {
    "AllowedOrigins": ["https://det.example.com"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

Replace the example origin with the deployed public frontend origin,
`https://{{ public_host }}` after resolving the inventory. Include the scheme
and any non-default port, without a path or trailing slash. Add development or
other frontend origins only as needed. `GET` covers the downloads; `HEAD`
permits header checks. S3 handles preflight `OPTIONS` automatically; do not
include it in `AllowedMethods`. See the [S3 CORS configuration reference](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ManageCorsUsing.html).

All configured URLs and referenced files must be browser-accessible over HTTPS
without an interactive login. The frontend supplies no S3 credentials or signed
requests. Configure read access separately: CORS does not grant object access.
Any gateway/CDN serving the bucket must forward origin/preflight headers, permit
`OPTIONS`, preserve CORS response headers, and cache responses appropriately
when they vary by origin.

To verify, reload the public frontend with the browser Network tab open and try
address search. Check the external manifests, tile files, and SQLite download
for successful responses with `Access-Control-Allow-Origin` matching the
frontend origin (or `*` for deliberately unrestricted CORS). Opening a file URL
in a browser tab alone does not verify CORS. Check permissions and paths for
`403`/`404` responses; a flat globe can indicate failed terrain loading.

## Backend Environment

### `tiles_url`

Base URL of the external tiles server. The backend publishes this base URL
to the public frontend through the map-resources endpoint and retains the
legacy tile redirect route for compatibility.
Must be set explicitly — there is no default, as this is an external service that varies per deployment.

```yaml
tiles_url: "https://tiles.example.com"
```

### `terrain_url`

Base URL of the external Cesium terrain server. The backend publishes it to
the public frontend through the map-resources endpoint and retains the legacy
terrain redirect route for compatibility.

```yaml
terrain_url: "https://terrain.example.com"
```

### `address_database_url`

URL of the SQLite address database downloaded by the public frontend. The
backend publishes it together with the tiles and terrain URLs through the
map-resources endpoint.

```yaml
address_database_url: "https://tiles.example.com/det-rg-addresses.sqlite"
```

## Feedback mail transport

Set `feedback_mail_to` under `inv_addons.digital-energy-twin` to provision
the backend mail transport. When it is omitted, the addon does not set any
mail environment variables. The current backend mail service is not yet
connected to feedback submissions, so this setting alone sends no mail.

```yaml
inv_addons:
  digital-energy-twin:
    feedback_mail_to: "feedback@example.com"
```

The addon reads the host, port, credentials, sender address, and `starttls`
from Civitas Core `inv_email`. With `starttls: true`, it creates an
`smtp://` URL; the backend requires STARTTLS before sending. With
`starttls: false`, it creates an `smtps://` URL for TLS from connection
start. Both modes require TLS. The SMTP URL is stored in a Kubernetes
Secret and supplied only to the backend container.

## Database

The addon supports three modes. The examples below show only database-related
inventory; merge the selected example into your normal Civitas Core inventory.

**1. Create the addon database on `central-db` (default)**

```yaml
inv_central_db:
  enable: true
  ns_name: "{{ ENVIRONMENT }}-database-stack"
  port: "5432"

inv_addons:
  digital-energy-twin:
    # Omit db entirely.
    enable: true
```

The addon requests `digital_energy_twin` and its dedicated owner user through
the Zalando Postgres operator on the platform's existing `central-db` cluster.
The backend uses the owner credentials, not the `postgres` superuser.

**2. Connect to an existing database**

```yaml
inv_addons:
  digital-energy-twin:
    db:
      mode: existing
      ns_name: "dev-databases"
      db_address: "postgres.dev-databases.svc.cluster.local"
      db_name: "digital_energy_twin"
      port: "5432"
      user_k8s_secret: "digital-energy-twin.credentials"
```

`mode: existing` is optional: existing inventories with `db` and no `mode`
continue to select this mode. `ns_name`, `db_address`, `db_name`, and
`user_k8s_secret` are required. `port` is optional and defaults to `5432`.
The referenced Secret in `db.ns_name`
must contain the keys `username` and `password`. The database and user must
already exist; the addon does not provision databases or roles in this mode.

**3. Create the addon database on another Zalando PostgreSQL cluster**

```yaml
inv_addons:
  digital-energy-twin:
    db:
      mode: zalando
      ns_name: "dev-databases"
      cluster_name: "energy-db"
      db_address: "energy-db.dev-databases.svc.cluster.local"
      db_name: "digital_energy_twin"
      port: "5432"  # Optional; defaults to 5432.
```

`ns_name`, `cluster_name`, `db_address`, and `db_name` are required.
`cluster_name` identifies an existing `acid.zalan.do/v1` `postgresql` resource
in `ns_name`, in the Kubernetes cluster selected by the addon's kubeconfig and
the platform context. `db_address` must point to that PostgreSQL cluster's
writable service. The addon creates a logical database within this cluster;
the platform operator remains responsible for deploying the PostgreSQL server.

The addon checks that the cluster exists and merges the requested database into
`spec.preparedDatabases`, preserving other database entries and cluster settings.
The Zalando operator creates the database and its default roles/users, and the
addon waits for the owner credentials Secret before configuring the backend.
Database names must start with a lowercase letter, contain only lowercase
letters, digits, and underscores, and be at most 51 characters long to accommodate
the generated role names.

With Civitas Core's operator defaults, the owner Secret in `ns_name` is named
`<database-with-hyphens>-owner-user.<cluster_name>.credentials.postgresql.acid.zalan.do`.
For this example it is
`digital-energy-twin-owner-user.energy-db.credentials.postgresql.acid.zalan.do`.
If the operator uses a custom naming template, set `db.user_k8s_secret` to the
generated owner Secret's name. It must contain `username` and `password`.

The addon's kubeconfig must allow reading and patching the target `postgresql`
resource and reading Secrets in its namespace. The Zalando operator must watch
that namespace and have database access enabled; Civitas Core's default operator
configuration watches all namespaces and enables database access. This mode
does not require `inv_central_db.enable` or the platform-wide `inv_mngd_db` switch.

In all three modes, the addon copies the assembled connection URL into the
`digital-energy-twin-database` Secret in its own namespace. The backend and its
migration init container consume `DATABASE_URL` through `secretKeyRef`.
The init container runs `zenstack migrate deploy` to apply the application schema.
Changing the selected server or database does not transfer existing data.

Existing installations that used the `postgres` database must migrate any data
that should be retained before switching. The addon initializes the new logical
database through the normal backend migrations, but it does not copy or delete
tables from the old `postgres` database.

Namespace behavior:

- `ns_create: true` -> addon creates the namespace if missing
- `ns_create: false` -> addon expects the namespace to exist and fails otherwise

## Matomo web analytics

The addon can deploy Matomo for the Digital Energy Twin's web analytics. Users
log in via the platform Keycloak; only users with the client role `matomoViewer`
or `matomoAdmin` of the addon client get in. The image
`ghcr.io/csi-foxbyte/regensburg-web-matomo` comes from the same monorepo release
manifest as the other images and is deployed only by its digest. Releases up to
v1.7.3 contain no Matomo image: with such a release, the addon skips Matomo and
says so in the playbook output. The image itself is described in the monorepo
under `images/matomo/README.md`.

### What the addon creates

All in the addon namespace, unless noted:

| Resource | Name | Purpose |
| --- | --- | --- |
| Deployment, Service | `digital-energy-twin-matomo`, `<matomo.host with dashes>` | Matomo on port 80, one pod (`Recreate`) |
| PVC | `digital-energy-twin-matomo-data` | `/var/www/html`, including `config/config.ini.php` (DB password, salt, client secret, "installed" marker). Losing it brings back the installer. |
| StatefulSet, Service | `digital-energy-twin-matomo-db` | MariaDB 11.8 (pinned by digest) |
| PVC | `digital-energy-twin-matomo-db-data` | MariaDB data |
| NetworkPolicy | `digital-energy-twin-matomo-db` | Only Matomo and its backup job reach MariaDB (if the CNI enforces policies) |
| NetworkPolicy | `digital-energy-twin-matomo` | Only pods in the APISIX namespace reach Matomo, so nobody can bypass the install lock from inside the cluster (if the CNI enforces policies) |
| Secret | `digital-energy-twin-matomo-db` | `password` and `root-password`, generated once and never changed by the addon |
| Secret | `digital-energy-twin-matomo-oidc` | `client-secret` of the Keycloak client, mounted into Matomo as a file |
| PVC, CronJob | `digital-energy-twin-matomo-backup` | Nightly backups, see [Backups](#matomo-backups) |
| Gateway/HTTPRoute or Ingress | `digital-energy-twin-matomo-host` | In the APISIX namespace, like the other hosts |
| APISIX upstream and route | `digital-energy-twin-matomo-upstream`, `-direct` | `matomo.host` to Matomo; locked until installed |
| Keycloak client, flow, roles | `matomo`, `matomo browser <hash>`, `matomoViewer`/`matomoAdmin` | See [Keycloak](#matomo-keycloak) |

The Matomo volume is `ReadWriteOnce` and mounted read-write only by the Matomo
pod. The backup job mounts only its `config/` directory, read-only, and runs on
the same node. Kubernetes itself does not stop other pods in the namespace from
mounting the volume: keep it that way through namespace RBAC and do not mount it
in other workloads.

### Configuration

```yaml
inv_addons:
  digital-energy-twin:
    matomo:
      enable: true
      host: "matomo.det.{{ DOMAIN }}"        # Default.
      installed: false                      # Set to true after the web installer.
      install_allowed_cidrs: ["192.0.2.10/32"]
      install_trusted_proxies: []           # See below.
      # keycloak_internal_url: "http://keycloak.<namespace>.svc.cluster.local:8080"
      storage_class: ""                     # Empty: the cluster's default StorageClass.
      data_size: "5Gi"
      db_size: "10Gi"
      tracking_enabled: false               # See step 8 of the runbook.
      site_id: 1
      backup:
        enable: true
        target: pvc                         # The only target so far.
        schedule: "30 2 * * *"
        keep_days: 14
        size: "10Gi"
```

- **Install lock.** Until the web installer has run, anyone reaching it could
  make themselves superuser. While `installed` is `false`, APISIX lets only
  `install_allowed_cidrs` through and answers everyone else with `403` (with an
  empty list: everyone). This needs APISIX to see the real client address. If
  another proxy sits in front of APISIX, list its addresses in
  `install_trusted_proxies`; APISIX then takes the client from
  `X-Forwarded-For` (plugin `real-ip`). The plugins `ip-restriction` and
  `real-ip` must be enabled in APISIX.
- **`keycloak_internal_url`** (optional) sets `MATOMO_OIDC_BACKCHANNEL_URL`, so
  Matomo calls the token and userinfo endpoints through the internal Keycloak
  service. Keycloak must still issue tokens for its public hostname
  (`inv_access.platform.hostname`), which it does when its hostname is
  configured. Without it, Matomo uses the public URL.
- `MATOMO_DISABLE_TRUSTED_HOST_CHECK` is never set; Matomo trusts the host the
  installer was opened with.
- The sizes apply when the PVCs are created. A PVC can grow later only if its
  StorageClass allows volume expansion.
- Reports are archived when someone opens them (Matomo's default, browser-triggered
  archiving). With a lot of traffic, a `core:archive` CronJob would be the next step.
- **Switching Matomo off.** `enable: false` removes the APISIX route, the Ingress or
  Gateway/HTTPRoute, Matomo, MariaDB and the backup job. The volumes, the Secrets
  and the Keycloak client, flow and roles stay, so `enable: true` brings back the
  same data. Delete the PVCs by hand to remove the data for good.

<a id="matomo-keycloak"></a>

### Keycloak

On every run the addon does the following through the Keycloak admin REST API,
like for the addon client:

- Creates the client roles `matomoViewer` and `matomoAdmin` on the addon client
  (`oidc_client_id`, default `digital-energy-twin`). `matomoAdmin` includes
  `matomoViewer`. Assign them like the other addon roles, see
  [Grant access in Keycloak](#grant-access-in-keycloak).
- Fails if the realm lets users edit their username ("Edit username" under
  *Realm settings > Login*). Matomo links Keycloak users to Matomo users by
  username, so a renamed account could take over another Matomo login.
- Creates and verifies the browser flow `matomo browser <hash>`. Matomo's LoginOIDC
  plugin cannot read roles, so this flow denies users without either role, also
  when they already have a Keycloak session:

  ```text
  matomo browser
  ├─ matomo authentication               REQUIRED
  │   ├─ Cookie                          ALTERNATIVE
  │   └─ matomo forms                    ALTERNATIVE
  │       ├─ Username Password Form      REQUIRED
  │       └─ matomo conditional otp      CONDITIONAL  (only if the realm's browser flow uses OTP)
  │           ├─ Condition - user configured  REQUIRED
  │           └─ OTP Form                REQUIRED
  │           (if the realm demands OTP from everyone: OTP Form REQUIRED directly under matomo forms)
  └─ matomo role gate                    CONDITIONAL
      ├─ Condition - user role  REQUIRED  <client>.matomoViewer, negated
      ├─ Condition - user role  REQUIRED  <client>.matomoAdmin, negated
      └─ Deny access            REQUIRED  "Kein Zugriff auf Matomo: …"
  ```

  The subflows carry the same `<hash>`, a short hash of the expected structure.
  If the realm's browser flow uses other second factors (WebAuthn, X.509,
  conditional OTP by role), the addon stops: the Matomo flow would skip them.
  When the expected structure changes (e.g. the realm turns on OTP), or the flow
  was changed by hand or left incomplete, the addon builds a new flow beside the
  old one and verifies it. It switches the client to the new flow only then, and
  removes the old flows afterwards. The client is never without the role gate.
- Creates or updates the client `matomo`: confidential, standard flow only (no
  direct access grants, implicit flow, service account or device grant), redirect
  URI `https://<matomo.host>/index.php?module=LoginOIDC&action=callback&provider=oidc`,
  post-logout redirect URI `https://<matomo.host>/*`, default client scopes
  `basic`, `profile` and `email`, and the browser flow override `matomo browser <hash>`.
  Its secret goes into the Secret `digital-energy-twin-matomo-oidc`; Matomo
  restarts when it changes.
- **Checks the role gate by logging in.** A misspelled role name in the flow
  would let everyone in (Keycloak only logs "Invalid role name submitted"). The
  addon therefore creates two temporary users (`matomo-gate-check-…`), one
  without a Matomo role and one with `matomoViewer`. It logs in as both through
  the `matomo` client's browser flow and deletes them again, also when the check
  fails. The run fails with `SECURITY: …` if the user without a role gets
  through, and also if the user with the role is refused, because then the first
  result proves nothing. The users need nothing but a password; a realm that
  requires other attributes or actions when users are created can make the check
  fail. Before these steps the addon fetches a fresh Keycloak admin token, so it
  cannot expire during them and leave the temporary users behind.

### Runbook for each customer

Prerequisites: a release with Matomo is adopted, DNS for `matomo.host` points to
the platform entry, and section 12 of the customer's privacy notice is at least
being prepared. Replace `NS` with the addon namespace and
`https://matomo.example.com` with `https://<matomo.host>`.

1. **Deploy, locked.** Set `matomo.enable: true`, `installed: false`, and
   `install_allowed_cidrs` to the address you install from. Run the playbook.
   It must finish without errors, including the Keycloak check. Pods:
   `kubectl -n NS get pods -l 'app in (digital-energy-twin-matomo, digital-energy-twin-matomo-db)'`.
2. **Install once.** Open `https://matomo.example.com` from the allowed
   address. The database settings are prefilled. Create the superuser as the
   break-glass account, with a login that can never be a Keycloak username, e.g.
   `matomo-breakglass`. Store its password in the customer inventory's Ansible
   Vault. As first website, enter the public frontend `https://<public_host>`;
   its ID (usually `1`) is `site_id`. Ignore the tracking code: the frontend has
   its own tracker. Then log in and turn on two-factor authentication for the
   break-glass account (*Administration > Personal > Security*). It logs in with
   its password only: Matomo does not let superusers log in via Keycloak.
3. **Unlock and restart.** Set `installed: true` and run the playbook. The route
   opens and the backup job is created. Restart Matomo so that its entrypoint
   activates the plugins, and check the log:

   ```bash
   kubectl -n NS rollout restart deployment/digital-energy-twin-matomo
   kubectl -n NS rollout status deployment/digital-energy-twin-matomo
   kubectl -n NS logs deployment/digital-energy-twin-matomo | grep 'Activated plugin'
   # Expected: Activated plugin LoginOIDC
   #           Activated plugin NoPasswordReset
   ```

   The login page now shows a **Keycloak** button. *Lost your password?* is
   blocked by NoPasswordReset.
4. **Privacy configuration.** Apply the same settings as the monorepo's
   `apps/frontend/tests/matomo/configure.spec.ts`, which section 12 of the
   privacy notice describes. In the UI (*Administration > Privacy* and
   *Administration > System > Geolocation*), or with the API calls below:

   | Setting | Value |
   | --- | --- |
   | Anonymize visitors' IP addresses | on, mask 4 bytes, also for enrichment |
   | Anonymize User ID and Order ID | on |
   | Referrer | do not store (`exclude_all`) |
   | Disable all tracking cookies (`forceCookielessTracking`) | **off**, required by the DEZ tracker |
   | Randomize config_id | off |
   | Geolocation | disabled |
   | Compliance policy `cnil_v1` (enforced) | as on the devcontainer instance, unless section 12 says otherwise |

   `[Tracker] trust_visitors_cookies = 1` is already set by the image.

   ```bash
   M=https://matomo.example.com/index.php
   TOKEN=...      # Break-glass auth token: Administration > Personal > Security > Auth tokens
   PW=...         # Break-glass password from the Ansible Vault (asked as confirmation)
   api() { curl -fsS "$M" -d module=API -d format=json -d "token_auth=$TOKEN" --data-urlencode "passwordConfirmation=$PW" "$@"; echo; }
   api -d method=PrivacyManager.setAnonymizeIpSettings -d anonymizeIPEnable=1 -d ipAddressMaskLength=4 \
     -d useAnonymizedIpForVisitEnrichment=1 -d anonymizeUserId=1 -d anonymizeOrderId=1 \
     -d anonymizeReferrer=exclude_all -d forceCookielessTracking=0 -d randomizeConfigId=0
   api -d method=UserCountry.setLocationProvider -d providerId=disabled
   api -d method=PrivacyManager.setComplianceStatus -d idSite=1 -d complianceType=cnil_v1 -d enforce=1
   ```
5. **Matomo users.** Keycloak only checks the role; Matomo also needs a user.
   For each person with `matomoViewer` or `matomoAdmin`, create a Matomo user
   whose login is exactly their Keycloak username (lower case), with a random
   password nobody keeps, and give it *View* or *Admin* on the DEZ site. Their
   first Keycloak login links the accounts. Keycloak users without a Matomo user
   are rejected by Matomo.

   ```bash
   LOGIN=jdoe EMAIL=jdoe@example.com ACCESS=view   # or admin
   api -d method=UsersManager.addUser -d "userLogin=$LOGIN" -d "email=$EMAIL" \
     --data-urlencode "password=$(openssl rand -base64 36)"
   api -d method=UsersManager.setUserAccess -d "userLogin=$LOGIN" -d "access=$ACCESS" -d idSites=1
   ```
6. **Check the role gate.** Every playbook run checks it automatically (see
   [Keycloak](#matomo-keycloak)). Check it by hand once: in a private browser
   window, log in via **Keycloak** as a user without either role. Keycloak must
   show "Kein Zugriff auf Matomo: …" and must not return to Matomo. A user with
   the role and a Matomo user gets in.
7. **When a role is removed**, also delete or downgrade the Matomo user. The
   Keycloak flow blocks the next login, but an open Matomo session lasts until it
   expires.

   ```bash
   api -d method=UsersManager.deleteUser -d "userLogin=$LOGIN"
   # or: api -d method=UsersManager.setUserAccess -d "userLogin=$LOGIN" -d access=noaccess -d idSites=1
   ```
8. **Tracking.** Only once section 12 of the privacy notice is complete for
   this customer: set `tracking_enabled: true` and `site_id`, and run the
   playbook. The backend then gets `MATOMO_URL=https://<matomo.host>/matomo.php`
   and `MATOMO_SITE_ID`. Without them it reports no analytics configuration, and
   the public frontend neither offers web analytics nor sends anything.
   `https://<public_host>/api/public/analytics-config` shows the active setting.

<a id="matomo-backups"></a>

### Backups

Civitas Core backs up only PostgreSQL, so the addon backs up Matomo itself. The
CronJob `digital-energy-twin-matomo-backup` exists once `installed` is `true`.
Before that there is nothing to back up. Every night (`backup.schedule`, cluster
time zone) it writes to the PVC `digital-energy-twin-matomo-backup`, directory
`matomo/`:

- `matomo-db-<UTC time>.sql.gz`: `mariadb-dump --single-transaction --routines`,
  consistent while Matomo keeps running.
- `matomo-config-<UTC time>.ini.php`: `config/config.ini.php` only. The plugins
  and `common.config.ini.php` are rewritten on every start.

Files older than `backup.keep_days` (default 14) are deleted, but only after a
new backup has succeeded. The job fails (non-zero exit code, failed Job in
`kubectl -n NS get jobs`) if `mariadb-dump` fails, or if the dump is damaged,
incomplete or contains no Matomo tables, or `config.ini.php` is missing. The job
runs next to the Matomo pod; while Matomo is scaled to 0 it cannot start and fails
after two hours. Suspend the CronJob for longer maintenance, as in the restore
steps below.

> **Limits.** The backup PVC is in the same cluster and usually on the same
> storage as Matomo. It does not protect against losing the cluster or its
> storage. Copying it off the cluster is still open; `backup.target` is there
> so that an object storage target (S3) can replace the PVC once Civitas Core
> provides one. The backup PVC contains `config.ini.php` with the database
> password and salt: protect it like the Matomo volume, with namespace RBAC and
> without access for other workloads. With node-local storage, the backup PVC
> and the Matomo volume must be on the same node, because the job runs next to
> Matomo.

Run a backup now, e.g. before an update:

```bash
kubectl -n NS create job --from=cronjob/digital-energy-twin-matomo-backup matomo-backup-manual
kubectl -n NS logs -f job/matomo-backup-manual     # "backup written: ..."
kubectl -n NS delete job matomo-backup-manual
```

**Restore** (replace `STAMP` with the chosen backup's time):

1. Stop Matomo and the backup job:

   ```bash
   kubectl -n NS scale deployment/digital-energy-twin-matomo --replicas=0
   kubectl -n NS patch cronjob digital-energy-twin-matomo-backup -p '{"spec":{"suspend":true}}'
   ```

   If the Matomo volume itself was lost, first start Matomo once on the new,
   empty volume (it copies Matomo into it and shows the installer; do not run
   it), then stop it again. If the Secret `digital-energy-twin-matomo-db` was
   lost too, recreate it with the `password` from the `[database]` section of the
   backed-up `config.ini.php` before MariaDB starts on an empty volume.
2. Start a helper pod. Its label lets it through the database NetworkPolicy:

   ```yaml
   apiVersion: v1
   kind: Pod
   metadata:
     name: matomo-restore
     labels:
       app: digital-energy-twin-matomo-backup
   spec:
     restartPolicy: Never
     containers:
       - name: restore
         image: docker.io/library/mariadb:11.8.9@sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15
         command: ["sleep", "3600"]
         env:
           - name: DB_PASSWORD
             valueFrom:
               secretKeyRef: {name: digital-energy-twin-matomo-db, key: password}
         volumeMounts:
           - {name: backup, mountPath: /backup, readOnly: true}
           - {name: data, mountPath: /var/www/html}
     volumes:
       - name: backup
         persistentVolumeClaim: {claimName: digital-energy-twin-matomo-backup}
       - name: data
         persistentVolumeClaim: {claimName: digital-energy-twin-matomo-data}
   ```
3. Import the dump and put `config.ini.php` back:

   ```bash
   kubectl -n NS exec matomo-restore -- ls -l /backup/matomo
   kubectl -n NS exec matomo-restore -- sh -c \
     'zcat /backup/matomo/matomo-db-STAMP.sql.gz | mariadb -h digital-energy-twin-matomo-db -u matomo -p"$DB_PASSWORD" matomo'
   kubectl -n NS exec matomo-restore -- sh -c \
     'cp /backup/matomo/matomo-config-STAMP.ini.php /var/www/html/config/config.ini.php && chown 33:33 /var/www/html/config/config.ini.php'
   ```
4. Clean up and start Matomo again:

   ```bash
   kubectl -n NS delete pod matomo-restore
   kubectl -n NS scale deployment/digital-energy-twin-matomo --replicas=1
   kubectl -n NS patch cronjob digital-energy-twin-matomo-backup -p '{"spec":{"suspend":false}}'
   kubectl -n NS logs deployment/digital-energy-twin-matomo | grep 'Activated plugin'
   ```

   Check that the login works and the reports are there.

**Test a restore once** per customer, without touching the running instance: import the
latest dump into a throwaway MariaDB and compare it with the live database.

```bash
kubectl -n NS apply -f - <<'YAML'
apiVersion: v1
kind: Pod
metadata:
  name: matomo-restore-test
spec:
  restartPolicy: Never
  containers:
    - name: mariadb
      image: docker.io/library/mariadb:11.8.9@sha256:6422478cb8e159f080fb1d8ccf65101e26fe51385787fde7d16c3b165a331f15
      env:
        - {name: MARIADB_ROOT_PASSWORD, value: restore-test}
        - {name: MARIADB_DATABASE, value: matomo}
      volumeMounts:
        - {name: backup, mountPath: /backup, readOnly: true}
  volumes:
    - name: backup
      persistentVolumeClaim: {claimName: digital-energy-twin-matomo-backup}
YAML
kubectl -n NS wait --for=condition=Ready pod/matomo-restore-test
kubectl -n NS exec matomo-restore-test -- sh -c \
  'until mariadb-admin -uroot -prestore-test ping >/dev/null 2>&1; do sleep 2; done &&
   zcat "$(ls /backup/matomo/matomo-db-2*.sql.gz | tail -n 1)" | mariadb -uroot -prestore-test matomo &&
   mariadb -uroot -prestore-test -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = \"matomo\"; SELECT idsite, name FROM matomo.matomo_site; SELECT login FROM matomo.matomo_user"'
kubectl -n NS exec matomo-restore-test -- sh -c 'grep -c "^\[database\]" "$(ls /backup/matomo/matomo-config-2*.ini.php | tail -n 1)"'
kubectl -n NS delete pod matomo-restore-test
```

The table count, sites and users must match the live database, which you can
read with `kubectl -n NS exec statefulset/digital-energy-twin-matomo-db -- sh -c 'mariadb -u matomo -p"$MARIADB_PASSWORD" matomo -e "SELECT idsite, name FROM matomo_site"'`.

### First rollout on a real cluster

The addon's Matomo tasks were tested offline: unit tests, Ansible rendering, the
containers with the same security settings in Docker, the backup and restore
script, and all Keycloak tasks against a local Keycloak 26.7. Check the parts
that need a real cluster at the first rollout:

- The PVCs are bound and the pods ready with the platform's StorageClass, and
  the backup job runs (manual job above) on the Matomo node.
- From an address outside `install_allowed_cidrs`, `https://<matomo.host>`
  answers `403` while `installed` is `false`; from an allowed address the
  installer opens. If the allowed address also gets `403`, APISIX does not see
  the client address: set `install_trusted_proxies`.
- If the CNI enforces NetworkPolicies, a pod other than Matomo cannot connect to
  `digital-energy-twin-matomo-db:3306`, and a pod outside the APISIX namespace
  cannot reach the Matomo pod on port 80, while Matomo still turns ready and
  APISIX still reaches it.
- The playbook passes the Keycloak checks against the platform realm, and steps
  3 to 6 of the runbook work.
- After step 8: with web analytics accepted in the public frontend, visits
  arrive in Matomo (the tracker posts cross-origin to `matomo.php`).

## Routing and Security

### Platform routing

Select the entry routing with `inv_addons.digital-energy-twin.routing_mode`.
Both modes send the addon hostnames (three, plus `matomo.host` when
[Matomo](#matomo-web-analytics) is deployed) to the
`{{ inv_access.apisix.helm_release_name }}-gateway` Service on port 80 in the
APISIX namespace (`inv_access.apisix.ns_name`). APISIX applies the same
routing and authentication rules in both modes, then forwards requests to the
application Services in the addon namespace.

| `routing_mode` | Resources created | Platform setting |
| --- | --- | --- |
| `gateway` (default) | `Gateway` and `HTTPRoute` per hostname | `inv_k8s.gateway_class` |
| `ingress` | Kubernetes `Ingress` per hostname | `inv_k8s.ingress_class` |

For a platform that requires Gateway API:

```yaml
inv_addons:
  digital-energy-twin:
    routing_mode: gateway
```

For an existing dev platform using ingress-nginx:

```yaml
inv_k8s:
  ingress_class: nginx  # Must match the existing platform IngressClass.

inv_addons:
  digital-energy-twin:
    routing_mode: ingress
```

These are partial inventory examples to merge into the existing configuration.
The addon does not install controllers or change how the platform exposes
APISIX, Keycloak, or other services. It does not depend on
`inv_access.apisix.ingress_controller.enable`. The existing `admin_host`,
`public_host`, and `backend_host` values work in either mode; their DNS must
reach the selected controller's entry point.

**Gateway mode** follows Civitas Core's `apisix-gateway-dataplane.yaml` pattern.
It requires Gateway API v1 and a running controller for `inv_k8s.gateway_class`
(Traefik in the reference configuration). The addon checks API availability,
not controller health or live routing, before creating resources. In this mode,
the addon does not read, create, or delete Kubernetes Ingress objects.

cert-manager must have
[Gateway API support enabled](https://cert-manager.io/docs/usage/gateway/).
Gateway annotations select `inv_k8s.cert_manager.issuer_name` for issuing
certificates into `<hostname>-tls` Secrets in the APISIX namespace.
`inv_k8s.ingress.http: false` (the default when unset) creates HTTP-to-HTTPS
redirect routes. When it is `true`, both HTTP and HTTPS forward to APISIX,
and obsolete Gateway redirect routes are removed.

**Ingress mode** requires an existing controller for `inv_k8s.ingress_class`.
It creates one standard `networking.k8s.io/v1` Ingress resource per hostname in the
APISIX namespace, with TLS certificates requested through the same cluster
issuer. It does not require `inv_k8s.gateway_class`, Gateway API definitions,
or a Gateway controller, and performs no Gateway API discovery or resource
operations. For ingress-nginx, the `ssl-redirect` annotation follows
`inv_k8s.ingress.http`; other Ingress controllers use their platform redirect
configuration.

The kubeconfig in `inv_access.apisix.ns_kubeconfig` must allow reading, creating,
and updating the selected route resource types in the APISIX namespace.
Gateway mode also needs permission to delete obsolete redirect HTTPRoutes.
The inventory keys `inv_k8s.ingress.http` and `inv_k8s.ingress.ca_path` retain
the platform's naming; they control HTTP behavior and CA trust.

Changing modes does not automatically remove resources from the previous mode.
After verifying the new routes, the platform operator should remove obsolete
Ingress or Gateway/HTTPRoute resources named `digital-energy-twin-admin-host`,
`digital-energy-twin-public-host`, `digital-energy-twin-backend-host`, and
`digital-energy-twin-matomo-host`, plus
Gateway HTTPRoutes with the `-redirect` suffix where applicable. Routes live in
the APISIX namespace; older addon versions also used the addon namespace.

### APISIX route matrix

`admin_host`:

- `/*` -> admin frontend (OIDC required + one of `admin`, `manager`, or `maintainer`)
- `/api/*` -> backend (OIDC required + one of `admin`, `manager`, or `maintainer`)

`public_host`:

- `/*` -> public frontend (open, no OIDC)
- `/api/admin/*` -> blocked
- `/api/*` -> backend (open, no OIDC)

`backend_host`:

- `/api/admin/*` -> protected with APISIX `openid-connect` and one of `admin`, `manager`, or `maintainer`
- `/api/*` -> open, except for the higher-priority `/api/admin/*` route
- `/docs` -> open
- `/docs/*` -> open

`matomo.host` (only when Matomo is deployed):

- `/*` -> Matomo. Until `matomo.installed: true`, only `matomo.install_allowed_cidrs`
  get through; everyone else gets `403`. Afterwards it is open, and Matomo and
  Keycloak handle the login.

Application permissions and role assignment are described in
[Admin frontend access](#admin-frontend-access).

### Backend APISIX plugins

Applied on backend routes:

- `cors`
- `limit-count`
- `response-rewrite` (security headers)

CORS defaults to:

- `https://{{ public_host }}`
- `https://{{ admin_host }}`

You can extend allowed origins with `backend_cors_additional_origins`.

## Security Requirements

- Non-root container runtime is enforced via pod/container `securityContext` in all deployment templates,
  with one exception: the Matomo container starts as root, because its entrypoint fills and
  `chown`s the volume before Apache serves as `www-data`. It drops all capabilities except
  `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`, `NET_BIND_SERVICE` and `KILL`, and
  runs with a read-only root filesystem. MariaDB and the backup job run as non-root (UID 999).
- Images must support non-root execution.

## Deploy script

`scripts/deploy.sh` is a helper that SSHes into your Ansible host, pulls the latest repo state, and runs the playbook.

Copy `scripts/.env.example` (or create `scripts/.env`) with the following variables:

```bash
SSH_HOST=your-deploy-server
SSH_USER=deploy
SSH_PORT=22               # optional, defaults to 22
ADDON_DIR=/path/to/this/addon
PLAYBOOK_DIR=/path/to/ansible/project
SOURCE_DIR=/path/to/venv
INVENTORY=/path/to/inventory.yml
PLAYBOOK_FILE=core_platform/playbook.yml
TAGS=addons
```

Leave `TAGS` empty to run the complete platform playbook. `TAGS=addons` runs all
addons configured in `inv_addons.addons`.

Then run:

```bash
./scripts/deploy.sh
```

## Software bill of materials

The checked-in [`SBOM.cdx.json`](SBOM.cdx.json) is a CycloneDX 1.6 inventory;
[`SBOM.csv`](SBOM.csv) provides the same components in a review-friendly table.
It covers the addon and all selected application images (Matomo from the first
release that contains it), the MariaDB image used for Matomo,
the software needed to execute its Ansible tasks, and the platform services the
deployed app directly uses. The application inventories remain separate CycloneDX documents
under `sboms/`; the addon SBOM connects them to the corresponding containers
with hashed BOM-Link references.

Generate both files with isolated Python tooling:

```bash
python3 -m venv .sbom-env
.sbom-env/bin/python -m pip install -r requirements-sbom.txt
.sbom-env/bin/python scripts/generate_sbom.py
```

Container references come from `vars/application_release.json` after adoption;
the current selection still comes from `vars/software_references.yml`.
Update `sbom.config.json` whenever the addon's control-plane or platform
integrations change; the generator fails if a new image key has no SBOM entry.
Set `SBOM_REQUIRE_CHILD_BOMS=1` to require a verified child document for every selected image.

The manually triggered **Release addon from application release** workflow accepts
an exact completed monorepo release tag. Current monorepo releases are prereleases,
so select **Allow a completed application prerelease** when releasing v1.7.3.
The workflow checks out the selected monorepo tag, verifies that its manifest
matches the completed GitHub release journal, and checks the release SBOM hashes
and image identities. It records the full manifest and digest-pinned images,
bundles the matching child SBOMs, and sets the addon version to the same tag as
the monorepo release. It commits these files directly to `main`, then creates the
matching addon Git tag and GitHub Release. The GitHub Release is marked as a
prerelease when the selected monorepo release is a prerelease. The workflow
reuses the committed SBOM on a rerun when the selected release files are unchanged.

The repository must allow `GITHUB_TOKEN` to push to `main` and create tags and
releases. `.copier-answers.yml` is deliberately not changed by the release
workflow: its `_commit` value tracks the Civitas addon-template revision rather
than the addon release version.
