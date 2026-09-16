# Digital Energy Twin Addon

This addon deploys the Digital Energy Twin stack into Civitas Core:

- Public frontend container
- Admin frontend container
- Backend container

The platform's Gateway API controller routes incoming traffic to APISIX using
dedicated hosts. APISIX applies authentication and API policies before forwarding
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

### Database

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

The addon follows Civitas Core's `apisix-gateway-dataplane.yaml` pattern. It
creates one `gateway.networking.k8s.io/v1` `Gateway` per addon hostname, with
`HTTPRoute` resources pointing to the
`{{ inv_access.apisix.helm_release_name }}-gateway` Service on port 80. All these
resources live in the platform's APISIX namespace (`inv_access.apisix.ns_name`).
APISIX then forwards requests to the application Services in the addon namespace.

Gateways use `inv_k8s.gateway_class`, which must identify a GatewayClass served
by the platform's running Gateway API controller (Traefik in the reference
configuration). Gateway API v1 support is checked before the addon creates
resources. This checks API availability, not controller health or live routing.
The addon creates no Kubernetes `Ingress` resources and does not depend on
`inv_access.apisix.ingress_controller.enable`.

The kubeconfig in `inv_access.apisix.ns_kubeconfig` must allow reading, creating,
and updating Gateways and HTTPRoutes in the APISIX namespace, plus deleting
HTTPRoutes when removing an obsolete redirect. cert-manager must have
[Gateway API support enabled](https://cert-manager.io/docs/usage/gateway/).
The Gateway annotations select `inv_k8s.cert_manager.issuer_name` for issuing
the host certificates into `<hostname>-tls` Secrets in the same namespace.

As in Civitas Core, `inv_k8s.ingress.http: false` (the default when unset)
creates HTTP-to-HTTPS redirect routes. When it is `true`, both HTTP and HTTPS
forward to APISIX, and obsolete redirect routes are removed. The inventory keys
`inv_k8s.ingress.http` and `inv_k8s.ingress.ca_path` retain the platform's naming;
they control HTTP behavior and CA trust and do not enable Kubernetes Ingress.

The existing `admin_host`, `public_host`, and `backend_host` addon inventory
requires no changes. DNS for those hosts must reach the platform's Gateway
entry point. The platform must also expose its Keycloak and APISIX Admin API
endpoints, which the addon uses during deployment; their Gateway resources
remain the platform's responsibility.

Upgrading does not automatically prune previously deployed Ingress objects.
After verifying the Gateway routes, the platform operator can remove the old
`digital-energy-twin-admin-host`, `digital-energy-twin-public-host`, and
`digital-energy-twin-backend-host` Ingress objects from the APISIX namespace
(or the addon namespace for older deployments). This addon no longer reads,
creates, or deletes Ingress objects.

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
