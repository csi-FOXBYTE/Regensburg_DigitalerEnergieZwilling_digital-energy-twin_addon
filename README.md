# Digital Energy Twin Addon

This addon deploys the Digital Energy Twin stack into Civitas Core:

- Public frontend container
- Admin frontend container
- Backend container

Ingress traffic is routed via APISIX using dedicated hosts.

## Installation

Place this addon in the Civitas Core addons folder and import it via `inv_addons`.

## Configuration

Main config lives under `inv_cust.digital-energy-twin`.
Defaults are defined in [default_inventory.yml](default_inventory.yml).

Minimal example:

```yaml
inv_cust:
  digital-energy-twin:
    enable: true
    ns_name: "{{ inv_access.apisix.ns_name }}"
    ns_kubeconfig: "{{ kubeconfig_file }}"
    public_requires_login: true
    oidc_client_id: "digital-energy-twin"
    admin_host: "admin.det.{{ DOMAIN }}"
    public_host: "det.{{ DOMAIN }}"
    backend_host: "api.det.{{ DOMAIN }}"
    admin_image: "nginx:alpine"
    public_image: "ghcr.io/csi-foxbyte/regensburg_digitalerenergiezwilling_frontend:dev"
    backend_image: "ghcr.io/csi-foxbyte/regensburg_digitalerenergiezwilling_backend:dev"
    backend_cors_additional_origins: []
```

## Routing and Security

### Hosts

- `admin_host` -> admin frontend
- `public_host` -> public frontend
- `backend_host` -> backend API

### APISIX route matrix

`admin_host`:

- `/*` -> admin frontend
- `/api/*` -> backend (OIDC required + role `admin`)

`public_host`:

- `/*` -> public frontend
- `/api/*` -> backend (OIDC required + role `viewer`) when `public_requires_login: true`
- `/api/*` -> backend (open, no OIDC) when `public_requires_login: false`

`backend_host`:

- `/api/public/*` -> open
- `/docs` -> open
- `/docs/*` -> open
- `/api/admin/*` -> protected with APISIX `openid-connect` (auth at gateway, no explicit role check in APISIX)

### Frontend role checks

The addon ensures client roles exist in Keycloak for the OIDC client:

- `admin`
- `viewer`

Role checks on frontend routes in APISIX:

- `admin_host /api/*` requires role `admin`
- `public_host /api/*` requires role `viewer` when `public_requires_login: true`

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

## Execute

Run the normal Civitas Core playbook.  
To run addon tasks explicitly, use tags:

- `addons`
- `addon_digital-energy-twin`
