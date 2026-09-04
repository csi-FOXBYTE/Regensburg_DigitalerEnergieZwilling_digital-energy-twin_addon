# Digital Energy Twin Addon

This addon deploys the Digital Energy Twin stack into Civitas Core:

- Public frontend container
- Admin frontend container
- Backend container

Ingress traffic is routed via APISIX using dedicated hosts.

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
as the single source of truth.

### Database

If `inv_addons.digital-energy-twin.db` is omitted, the addon uses the platform
`central-db`. It declaratively creates a `digital_energy_twin` database and a
dedicated owner user through the Zalando Postgres operator. The backend does not
use the `postgres` database or its superuser credentials.

To use a database managed by the platform operator, define `db` explicitly:

```yaml
inv_addons:
  digital-energy-twin:
    db:
      ns_name: "dev-databases"
      db_address: "postgres.dev-databases.svc.cluster.local"
      db_name: "digital_energy_twin"
      port: "5432"
      user_k8s_secret: "digital-energy-twin.credentials"
```

`ns_name`, `db_address`, `db_name`, and `user_k8s_secret` are required in
managed mode. `port` is optional and defaults to `5432`. The referenced Secret
must contain the keys `username` and `password`. The database and user must
already exist; managed mode does not modify the supplied PostgreSQL server.

In both modes, the addon copies the assembled connection URL into the
`digital-energy-twin-database` Secret in its own namespace. The backend and its
migration init container consume `DATABASE_URL` through `secretKeyRef`.

Existing installations that used the `postgres` database must migrate any data
that should be retained before switching. The addon initializes the new logical
database through the normal backend migrations, but it does not copy or delete
tables from the old `postgres` database.

Namespace behavior:

- `ns_create: true` -> addon creates the namespace if missing
- `ns_create: false` -> addon expects the namespace to exist and fails otherwise

## Routing and Security

### Hosts

- `admin_host` -> admin frontend
- `public_host` -> public frontend
- `backend_host` -> backend API

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

### OIDC client roles and route checks

The addon ensures client roles exist in Keycloak for the OIDC client:

- `admin`
- `manager`
- `maintainer`

The obsolete `viewer` client role is removed during deployment.

Role checks in APISIX:

- `admin_host /*` and `admin_host /api/*` require at least one of `admin`, `manager`, or `maintainer`
- `backend_host /api/admin/*` requires at least one of `admin`, `manager`, or `maintainer`
- `public_host` is open, while `/api/admin/*` is explicitly blocked on that host

Note: roles are created automatically, but user/group role assignments are not managed by this addon.

### Backend APISIX plugins

Applied on backend routes:

- `cors`
- `limit-count`
- `response-rewrite` (security headers)

CORS defaults to:

- `https://{{ public_host }}`
- `https://{{ admin_host }}`

You can extend allowed origins with `backend_cors_additional_origins`.

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

## Security Requirements

- Non-root container runtime is enforced via pod/container `securityContext` in all deployment templates.
- Images must support non-root execution.

## Execute

Run the normal Civitas Core playbook. To run addon tasks explicitly, use one of
these tags:

- `addons` for all configured addons
- `addon_digital-energy-twin` for this addon only

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
