"""Offline gateway security checks; requires Jinja2 and PyYAML (Ansible dependencies)."""

import base64
import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["urlsplit"] = lambda value, part: getattr(urlsplit(value), part)
ENV.filters["unique"] = lambda values: list(dict.fromkeys(values))
ENV.filters["combine"] = lambda *dicts: {key: value for d in dicts for key, value in d.items()}
ENV.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).strip().lower() in ("yes", "on", "1", "true")

PROTECTED_ROUTES = (
    "Upsert protected admin direct-host route",
    "Upsert admin-host API route (OIDC protected)",
    "Upsert backend admin-api route (OIDC protected)",
)


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def render(text, **context):
    return ENV.from_string(text).render(context)


def route_plugins(suffix):
    return task(suffix)["uri"]["body"]["plugins"]


def public_csp(matomo_enabled):
    origins = render(
        task("Set origins the public frontend connects to")["ansible.builtin.set_fact"][
            "digital_energy_twin_public_connect_origins"
        ],
        digital_energy_twin={
            "tiles_url": "https://tiles.example.test/3d",
            "terrain_url": "https://terrain.example.test",
            "address_database_url": "https://tiles.example.test/det-rg-addresses.sqlite",
        },
        digital_energy_twin_matomo_host="matomo.det.example.test",
        digital_energy_twin_matomo_enabled=matomo_enabled,
    )
    facts = task("Set security headers for the frontend HTML")["ansible.builtin.set_fact"]
    return render(facts["digital_energy_twin_public_csp"], digital_energy_twin_public_connect_origins=origins)


def directives(policy):
    return {part.split()[0]: part.split()[1:] for part in (p.strip() for p in policy.split(";")) if part}


class GatewaySecurityTests(unittest.TestCase):
    def test_both_frontends_send_security_headers_with_their_own_csp(self):
        for route, csp in (
            ("Upsert public direct-host route (open)", "digital_energy_twin_public_csp"),
            ("Upsert protected admin direct-host route", "digital_energy_twin_admin_csp"),
        ):
            with self.subTest(route=route):
                headers = route_plugins(route)["response-rewrite"]["headers"]["set"]
                self.assertIn("digital_energy_twin_html_security_headers", headers)
                self.assertIn(csp, headers)
        facts = task("Set security headers for the frontend HTML")["ansible.builtin.set_fact"]
        self.assertEqual(facts["digital_energy_twin_html_security_headers"]["X-Frame-Options"], "DENY")
        self.assertEqual(facts["digital_energy_twin_html_security_headers"]["X-Content-Type-Options"], "nosniff")
        for name in ("digital_energy_twin_public_csp", "digital_energy_twin_admin_csp"):
            policy = directives(render(facts[name], digital_energy_twin_public_connect_origins=""))
            with self.subTest(policy=name):
                self.assertEqual(policy["frame-ancestors"], ["'none'"])
                self.assertEqual(policy["object-src"], ["'none'"])
                self.assertEqual(policy["base-uri"], ["'self'"])
                self.assertEqual(policy["default-src"], ["'self'"])

    def test_public_csp_allows_the_configured_data_hosts_and_matomo_only_when_enabled(self):
        enabled = directives(public_csp(matomo_enabled=True))["connect-src"]
        self.assertIn("https://tiles.example.test", enabled)
        self.assertIn("https://terrain.example.test", enabled)
        self.assertIn("https://intergeo38.bayernwolke.de", enabled)
        self.assertIn("https://matomo.det.example.test", enabled)
        self.assertEqual(enabled.count("https://tiles.example.test"), 1)
        self.assertNotIn("https://matomo.det.example.test", directives(public_csp(matomo_enabled=False))["connect-src"])

    def test_admin_csp_needs_no_inline_scripts(self):
        facts = task("Set security headers for the frontend HTML")["ansible.builtin.set_fact"]
        policy = directives(render(facts["digital_energy_twin_admin_csp"]))
        self.assertNotIn("'unsafe-inline'", policy["script-src"])
        self.assertEqual(policy["connect-src"], ["'self'"])

    def test_sessions_never_fall_back_to_the_apisix_admin_key(self):
        text = (ROOT / "tasks/digital-energy-twin.yml").read_text()
        self.assertNotIn("default(inv_access.apisix.api_credentials.admin_role)", text)
        for route in PROTECTED_ROUTES:
            with self.subTest(route=route):
                self.assertIn("digital_energy_twin_oidc_session", route_plugins(route)["openid-connect"]["session"])

    def test_session_secret_is_generated_once_and_kept(self):
        create = task("Create OIDC session secret once")
        self.assertIn("resources | length == 0", create["when"])
        self.assertTrue(create["no_log"])
        select = task("Select OIDC session form")["ansible.builtin.set_fact"]
        key = render(
            select["digital_energy_twin_oidc_session_key"],
            digital_energy_twin_oidc_session_secret_info={"resources": [{"data": {"secret": base64.b64encode(b"kept").decode()}}]},
        )
        self.assertEqual(key, "kept")

    def test_cookie_is_secure_and_http_only_where_apisix_takes_cookie_options(self):
        settings = task("Set OIDC session settings")["ansible.builtin.set_fact"]["digital_energy_twin_oidc_session"]
        flat = ENV.compile_expression(settings.strip()[2:-2])(digital_energy_twin_oidc_session_key="k", digital_energy_twin_oidc_session_flat=True)
        self.assertEqual(flat, {"secret": "k", "cookie_path": "/", "cookie_secure": True, "cookie_http_only": True, "cookie_same_site": "Lax"})
        legacy = ENV.compile_expression(settings.strip()[2:-2])(digital_energy_twin_oidc_session_key="k", digital_energy_twin_oidc_session_flat=False)
        self.assertEqual(legacy, {"secret": "k"})
        schema = task("Select OIDC session form")["ansible.builtin.set_fact"]["digital_energy_twin_oidc_session_flat"]
        supported = {"json": {"properties": {"session": {"properties": {"secret": {}, "cookie_secure": {}}}}}}
        self.assertEqual(render(schema, digital_energy_twin_oidc_schema_response=supported).strip(), "True")
        old = {"json": {"properties": {"session": {"properties": {"secret": {}, "cookie": {}}}}}}
        self.assertEqual(render(schema, digital_energy_twin_oidc_schema_response=old).strip(), "False")

    def test_all_protected_routes_share_one_gate_that_checks_role_then_session(self):
        for route in PROTECTED_ROUTES:
            with self.subTest(route=route):
                functions = route_plugins(route)["serverless-post-function"]["functions"]
                self.assertEqual(functions, ["{{ digital_energy_twin_admin_gate_function }}"])
        gate = task("Set the admin gate for protected routes")["ansible.builtin.set_fact"]["digital_energy_twin_admin_gate_function"]
        self.assertIn("/protocol/openid-connect/token/introspect", gate)
        self.assertIn("invalid_stale = true", gate)
        body = gate[gate.index("return function()"):]
        self.assertLess(body.index("has_any_required_role_from_jwt(token)"), body.index("session_states(token"))
        self.assertIn('reject(401, "session ended")', body)
        self.assertIn('ngx.redirect("/logout")', body)


    def test_tls_to_keycloak_is_verified_unless_explicitly_switched_off(self):
        check = task("Validate the gateway TLS setting")["ansible.builtin.assert"]["that"][0]
        setting = task("Set defaults")["set_fact"]["digital_energy_twin_oidc_ssl_verify"]
        gate = task("Set the admin gate for protected routes")["ansible.builtin.set_fact"]["digital_energy_twin_admin_gate_function"]
        gate_line = next(line for line in gate.splitlines() if "ssl_verify" in line)
        for config, expected in (({}, True), ({"oidc_ssl_verify": True}, True), ({"oidc_ssl_verify": False}, False)):
            with self.subTest(config=config):
                self.assertTrue(ENV.compile_expression(check)(digital_energy_twin=config))
                verify = render(setting, digital_energy_twin=config) == "True"
                self.assertEqual(verify, expected)
                for route in PROTECTED_ROUTES:
                    plugin = route_plugins(route)["openid-connect"]
                    self.assertEqual(render(plugin["ssl_verify"], digital_energy_twin_oidc_ssl_verify=verify), str(expected))
                self.assertEqual(
                    render(gate_line, digital_energy_twin_oidc_ssl_verify=verify).strip(),
                    "ssl_verify = " + str(expected).lower(),
                )
        self.assertFalse(ENV.compile_expression(check)(digital_energy_twin={"oidc_ssl_verify": "false"}))
        self.assertNotIn("ssl_verify = false", gate)

    def test_admin_api_allows_cors_only_for_the_admin_origin(self):
        defaults = task("Set defaults")["set_fact"]
        config = {
            "public_host": "det.example.test",
            "admin_host": "admin.det.example.test",
            "backend_cors_additional_origins": ["https://partner.example.test"],
        }
        common = dict(defaults["digital_energy_twin_backend_common_plugins"]["cors"])
        common["allow_origins"] = render(common["allow_origins"], digital_energy_twin=config)
        self.assertEqual(
            common["allow_origins"].split(","),
            ["https://det.example.test", "https://admin.det.example.test", "https://partner.example.test"],
        )
        expression = task("Set CORS for the admin API")["ansible.builtin.set_fact"]["digital_energy_twin_backend_admin_cors"]
        admin = ENV.compile_expression(expression.strip()[2:-2])(
            digital_energy_twin_backend_common_plugins={"cors": common},
            digital_energy_twin_admin_direct_host="admin.det.example.test",
        )
        self.assertEqual(admin["allow_origins"], "https://admin.det.example.test")
        self.assertEqual({k: v for k, v in admin.items() if k != "allow_origins"},
                         {k: v for k, v in common.items() if k != "allow_origins"})
        for route in ("Upsert admin-host API route (OIDC protected)", "Upsert backend admin-api route (OIDC protected)"):
            with self.subTest(route=route):
                self.assertEqual(route_plugins(route)["cors"], "{{ digital_energy_twin_backend_admin_cors }}")
        for route in ("Upsert public-host API route (open)", "Upsert backend api route (open)"):
            with self.subTest(route=route):
                self.assertEqual(route_plugins(route), "{{ digital_energy_twin_backend_api_plugins }}")

    def test_public_host_admin_block_sends_the_api_security_headers(self):
        plugins = route_plugins("Upsert public-host admin-api block route")
        self.assertEqual(plugins["response-rewrite"], "{{ digital_energy_twin_backend_common_plugins['response-rewrite'] }}")
        self.assertIn("ngx.exit(403)", plugins["serverless-pre-function"]["functions"][0])
        headers = task("Set defaults")["set_fact"]["digital_energy_twin_backend_common_plugins"]["response-rewrite"]["headers"]["set"]
        for name in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options", "Content-Security-Policy"):
            self.assertIn(name, headers)


if __name__ == "__main__":
    unittest.main()
