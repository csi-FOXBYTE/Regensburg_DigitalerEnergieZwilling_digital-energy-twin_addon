"""Offline checks of the deletion link download TTL; requires Jinja2 and PyYAML."""

import re
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
SETTING = "submission_deletion_token_ttl_days"
FACT = "digital_energy_twin_" + SETTING
# Pattern of SUBMISSION_DELETION_TOKEN_TTL_DAYS in the backend env schema
# (apps/backend/fastify-toab.config.ts); the backend refuses to start otherwise.
BACKEND_PATTERN = re.compile(r"^[1-9][0-9]{0,3}$")


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def default_ttl(config):
    expression = task("Set defaults")["set_fact"][FACT]
    return ENV.from_string(expression).render(digital_energy_twin=config)


def render_backend(ttl):
    template = ENV.from_string((ROOT / "templates/digital_energy_twin_backend_deploy.yaml.j2").read_text())
    deployment = yaml.safe_load(template.render({
        "digital_energy_twin_backend_image": "backend@sha256:" + "b" * 64,
        "digital_energy_twin_database_secret_name": "db", "digital_energy_twin_backend_port": 5000,
        "digital_energy_twin_feedback_mail_to": "", "digital_energy_twin_tiles_url": "t",
        "digital_energy_twin_terrain_url": "t", "digital_energy_twin_address_database_url": "a",
        "digital_energy_twin_backend_direct_host": "api", "digital_energy_twin_public_direct_host": "det",
        "digital_energy_twin_admin_direct_host": "admin", "digital_energy_twin_oidc_client_id": "dez",
        "digital_energy_twin_backend_cors_additional_origins": "",
        "inv_access": {"platform": {"hostname": "https://idm"}, "tenant": {"realm_name": "dez"}},
        FACT: ttl,
    }))
    return {item["name"]: item.get("value")
            for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}


class DeletionLinkTtlTest(unittest.TestCase):
    def test_defaults_to_30_days(self):
        self.assertEqual(default_ttl({}), "30")
        self.assertEqual(default_ttl({SETTING: 7}), "7")
        self.assertEqual(default_ttl({SETTING: "90"}), "90")
        inventory = yaml.safe_load((ROOT / "default_inventory.yml").read_text())
        addon = inventory["all"]["children"]["controller"]["vars"]["inv_addons"]["digital-energy-twin"]
        self.assertEqual(addon[SETTING], 30)

    def test_validation_accepts_only_what_the_backend_accepts(self):
        validation = task("Validate the deletion link download TTL")
        self.assertEqual(validation["when"], f"digital_energy_twin.{SETTING} is defined")
        that = validation["ansible.builtin.assert"]["that"]
        for value in (1, 30, "30", 9999, 0, -1, 10000, "", "abc", "30d", " 30", 1.5, 30.0, True, False, None, [30]):
            with self.subTest(value=value):
                valid = all(ENV.compile_expression(condition)(digital_energy_twin={SETTING: value})
                            for condition in that)
                expected = not isinstance(value, bool) and BACKEND_PATTERN.match(str(value)) is not None
                self.assertEqual(valid, expected)
        names = [entry["name"].split(": ", 1)[1] for entry in TASKS]
        self.assertLess(names.index("Validate the deletion link download TTL"), names.index("Set defaults"))

    def test_backend_gets_the_ttl(self):
        self.assertEqual(render_backend(30)["SUBMISSION_DELETION_TOKEN_TTL_DAYS"], "30")
        self.assertEqual(render_backend("7")["SUBMISSION_DELETION_TOKEN_TTL_DAYS"], "7")


if __name__ == "__main__":
    unittest.main()
