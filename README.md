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
```

By default, the addon works without any `software` section in your deployment inventory.
Container images are resolved from [vars/software_references.yml](vars/software_references.yml)
as single source of truth.
Optional per-environment overrides are still possible by setting
`admin_image`, `public_image`, or `backend_image` in `inv_addons.digital-energy-twin`.

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

URL of the external tiles server. The backend uses this to redirect clients to the correct tile source.
Must be set explicitly — there is no default, as this is an external service that varies per deployment.

```yaml
tiles_url: "https://tiles.example.com"
```

## Security Requirements

- Non-root container runtime is enforced via pod/container `securityContext` in all deployment templates.
- Images must support non-root execution.

## Execute

Run the normal Civitas Core playbook.  
To run addon tasks explicitly, use tags:

- `addons`
- `addon_digital-energy-twin`

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
TAGS=addon
```

Then run:

```bash
./scripts/deploy.sh
```
