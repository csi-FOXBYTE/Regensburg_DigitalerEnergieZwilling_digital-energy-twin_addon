"""Offline checks of the gateway body size limit on the API routes; requires Jinja2 and PyYAML."""

import ast
import re
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["combine"] = lambda *dicts: {key: value for d in dicts for key, value in d.items()}
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
SETTING = "api_max_body_size"
FACT = "digital_energy_twin_" + SETTING
# Fastify's default bodyLimit; apps/backend/fastify-toab.config.ts sets none.
BACKEND_LIMIT = 1048576

# Every route to the backend that proxies API requests. The /docs routes, the
# public-host /api/admin/* block (answers 403 itself), the frontends and
# Matomo do not get the limit.
API_ROUTES = (
    "Upsert public-host API route (open)",
    "Upsert backend api route (open)",
    "Upsert admin-host API route (OIDC protected)",
    "Upsert backend admin-api route (OIDC protected)",
)


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def expression(text):
    return ENV.compile_expression(text.strip()[2:-2])


def route_tasks():
    return {
        entry["name"].split(": ", 1)[1]: entry["uri"]["body"]
        for entry in TASKS
        if entry.get("uri", {}).get("method") == "PUT" and "/apisix/admin/routes/" in entry["uri"]["url"]
    }


def limit_facts(config):
    """Render the setting as Ansible would, then the per-route plugin facts."""
    size = ENV.from_string(task("Set defaults")["set_fact"][FACT]).render(digital_energy_twin=config)
    facts = task("Set the body size limit for the API routes")["ansible.builtin.set_fact"]
    common = task("Set defaults")["set_fact"]["digital_energy_twin_backend_common_plugins"]
    context = {FACT: size, "digital_energy_twin_backend_common_plugins": common}
    # Without jinja2_native, Ansible turns a rendered dict back into one via literal_eval.
    rendered = ast.literal_eval(ENV.from_string(facts["digital_energy_twin_backend_client_control"]).render(context))
    return rendered, expression(facts["digital_energy_twin_backend_api_plugins"])(**context), common


class BodyLimitTest(unittest.TestCase):
    def test_defaults_to_the_backend_limit(self):
        for config, expected in (({}, BACKEND_LIMIT), ({SETTING: 524288}, 524288), ({SETTING: "65536"}, 65536)):
            with self.subTest(config=config):
                client_control, api_plugins, _ = limit_facts(config)
                self.assertEqual(client_control, {"max_body_size": expected})
                self.assertIs(type(client_control["max_body_size"]), int)
                self.assertEqual(api_plugins["client-control"], {"max_body_size": expected})
                self.assertIs(type(api_plugins["client-control"]["max_body_size"]), int)
        inventory = yaml.safe_load((ROOT / "default_inventory.yml").read_text())
        addon = inventory["all"]["children"]["controller"]["vars"]["inv_addons"]["digital-energy-twin"]
        self.assertEqual(addon[SETTING], BACKEND_LIMIT)

    def test_api_plugins_keep_the_common_plugins(self):
        _, api_plugins, common = limit_facts({})
        self.assertEqual({k: v for k, v in api_plugins.items() if k != "client-control"}, common)
        self.assertNotIn("client-control", common)

    def test_validation_accepts_whole_bytes_up_to_the_backend_limit(self):
        validation = task("Validate the API body size limit")
        self.assertEqual(validation["when"], f"digital_energy_twin.{SETTING} is defined")
        that = validation["ansible.builtin.assert"]["that"]
        for value in (1, 1024, "1024", BACKEND_LIMIT, str(BACKEND_LIMIT), BACKEND_LIMIT + 1, 2 * BACKEND_LIMIT,
                      0, -1, "", "abc", "1m", "1MiB", " 1024", 1.5, 1024.0, True, False, None, [1024]):
            with self.subTest(value=value):
                valid = all(ENV.compile_expression(condition)(digital_energy_twin={SETTING: value})
                            for condition in that)
                expected = (not isinstance(value, bool) and re.fullmatch(r"[1-9][0-9]*", str(value)) is not None
                            and int(value) <= BACKEND_LIMIT)
                self.assertEqual(valid, expected)
        names = [entry["name"].split(": ", 1)[1] for entry in TASKS]
        self.assertLess(names.index("Validate the API body size limit"), names.index("Set defaults"))
        self.assertLess(names.index("Set defaults"), names.index("Set the body size limit for the API routes"))
        self.assertLess(names.index("Set the body size limit for the API routes"), names.index(API_ROUTES[0]))

    def test_only_the_api_routes_get_the_limit(self):
        routes = route_tasks()
        for name in API_ROUTES:
            self.assertIn(name, routes)
        with_limit = set()
        for name, body in routes.items():
            plugins = body["plugins"]
            if plugins == "{{ digital_energy_twin_backend_api_plugins }}" or (
                isinstance(plugins, dict) and "client-control" in plugins
            ):
                with_limit.add(name)
        self.assertEqual(with_limit, set(API_ROUTES))
        for name in API_ROUTES:
            with self.subTest(route=name):
                body = routes[name]
                self.assertEqual(body["upstream_id"], "digital-energy-twin-backend-upstream")
                self.assertTrue(body["uri"].startswith("/api/"))

    def test_protected_api_routes_keep_their_plugins_next_to_the_limit(self):
        for name in ("Upsert admin-host API route (OIDC protected)", "Upsert backend admin-api route (OIDC protected)"):
            with self.subTest(route=name):
                plugins = route_tasks()[name]["plugins"]
                self.assertEqual(plugins["client-control"], "{{ digital_energy_twin_backend_client_control }}")
                self.assertEqual(plugins["cors"], "{{ digital_energy_twin_backend_admin_cors }}")
                self.assertEqual(
                    set(plugins),
                    {"client-control", "cors", "limit-count", "response-rewrite", "openid-connect", "serverless-post-function"},
                )


if __name__ == "__main__":
    unittest.main()
