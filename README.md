# Digital Energy Twin Addon

This addon deploys the Digital Energy Twin stack into Civitas Core:

- Public frontend container
- Admin frontend container
- Backend container

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
Container images are resolved from [vars/software_references.yml](vars/software_references.yml)
as the single source of truth. Each image has a concrete version `tag` and is
deployed as `registry/repository:tag`. The release workflow resolves moving tags
such as `dev` or `latest` to the corresponding version for each application.

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

## Routing and Security

### Platform routing

Select the entry routing with `inv_addons.digital-energy-twin.routing_mode`.
Both modes send the three addon hostnames to the
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
It creates three standard `networking.k8s.io/v1` Ingress resources in the
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
`digital-energy-twin-public-host`, and `digital-energy-twin-backend-host`, plus
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

- Non-root container runtime is enforced via pod/container `securityContext` in all deployment templates.
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
It covers the addon, all three application images referenced by version tag,
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

Container references are read directly from `vars/software_references.yml`.
Update `sbom.config.json` whenever the addon's control-plane or platform
integrations change; the generator fails if a new image key has no SBOM entry.
Set `SBOM_REQUIRE_CHILD_BOMS=1` to require all three verified child documents.

The manually triggered **Prepare application release** workflow accepts an
application image tag and an optional addon release tag. If the release tag is
empty, it selects the next minor semantic version (or `v0.1.0` when no release
tag exists). It reads each image's `org.opencontainers.image.version` label and
checks that the matching version tag resolves to the same digest as the requested
tag. It writes those concrete version tags to `vars/software_references.yml`,
verifies signed CycloneDX attestations using the resolved digests, bundles the
child SBOMs, updates the addon version, and opens a draft pull request for review.
It never merges the pull request or
creates the Git tag. After merging, create the selected addon tag manually.

The repository or organization must allow `GITHUB_TOKEN` to create pull
requests. `.copier-answers.yml` is deliberately not changed by the release
workflow: its `_commit` value tracks the Civitas addon-template revision rather
than the addon release version.
