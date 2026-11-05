"""Offline checks of the Altcha key for the public writes; requires Jinja2 and PyYAML."""

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).strip().lower() in ("yes", "on", "1", "true")
SECRET = "digital-energy-twin-altcha"


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def task_names():
    return [entry["name"].split(": ", 1)[1] for entry in TASKS]


def render_backend():
    template = ENV.from_string((ROOT / "templates/digital_energy_twin_backend_deploy.yaml.j2").read_text())
    deployment = yaml.safe_load(template.render({
        "digital_energy_twin_backend_image": "backend@sha256:" + "b" * 64,
        "digital_energy_twin_database_secret_name": "db", "digital_energy_twin_altcha_secret_name": SECRET,
        "digital_energy_twin_backend_port": 5000, "digital_energy_twin_submission_deletion_token_ttl_days": 30,
        "digital_energy_twin_feedback_mail_to": "", "digital_energy_twin_tiles_url": "t",
        "digital_energy_twin_terrain_url": "t", "digital_energy_twin_address_database_url": "a",
        "digital_energy_twin_backend_direct_host": "api", "digital_energy_twin_public_direct_host": "det",
        "digital_energy_twin_admin_direct_host": "admin", "digital_energy_twin_oidc_client_id": "dez",
        "digital_energy_twin_backend_cors_additional_origins": "",
        "inv_access": {"platform": {"hostname": "https://idm"}, "tenant": {"realm_name": "dez"}},
    }))
    return deployment["spec"]["template"]["spec"]["containers"][0]["env"]


class AltchaKeyTest(unittest.TestCase):
    def test_key_is_generated_once_and_kept_without_logging(self):
        defaults = task("Set defaults")["set_fact"]
        self.assertEqual(defaults["digital_energy_twin_altcha_secret_name"], SECRET)

        lookup = task("Get Altcha HMAC key secret")
        self.assertEqual(lookup["kubernetes.core.k8s_info"]["name"], "{{ digital_energy_twin_altcha_secret_name }}")
        self.assertEqual(lookup["register"], "digital_energy_twin_altcha_secret_info")
        self.assertTrue(lookup["no_log"])

        create = task("Create Altcha HMAC key secret once")
        self.assertTrue(create["no_log"])
        self.assertIn("digital_energy_twin_altcha_secret_info.resources | length == 0", create["when"])
        definition = create["kubernetes.core.k8s"]["definition"]
        self.assertEqual(create["kubernetes.core.k8s"]["namespace"], "{{ k8s_namespace }}")
        self.assertEqual(definition["kind"], "Secret")
        self.assertEqual(definition["metadata"]["name"], "{{ digital_energy_twin_altcha_secret_name }}")
        key = definition["stringData"]["ALTCHA_HMAC_KEY"]
        # 64 random letters and digits; the backend requires at least 32 characters.
        self.assertIn("lookup('ansible.builtin.password', '/dev/null', length=64", key)
        self.assertIn("chars=['ascii_letters', 'digits']", key)

        # The Secret exists before the backend starts, which refuses to start without it.
        names = task_names()
        self.assertLess(names.index("Get Altcha HMAC key secret"), names.index("Create Altcha HMAC key secret once"))
        self.assertLess(names.index("Create Altcha HMAC key secret once"), names.index("Ensure backend deployment"))

    def test_backend_reads_the_key_from_the_secret_and_never_disables_altcha(self):
        env = {item["name"]: item for item in render_backend()}
        self.assertEqual(env["ALTCHA_HMAC_KEY"], {
            "name": "ALTCHA_HMAC_KEY",
            "valueFrom": {"secretKeyRef": {"name": SECRET, "key": "ALTCHA_HMAC_KEY"}},
        })
        self.assertNotIn("ALTCHA_DISABLED", env)

    def test_gateway_lets_browsers_send_the_altcha_header(self):
        cors = task("Set defaults")["set_fact"]["digital_energy_twin_backend_common_plugins"]["cors"]
        self.assertIn("X-Altcha", cors["allow_headers"].split(","))

    def test_public_csp_lets_the_self_hosted_widget_run(self):
        facts = task("Set security headers for the frontend HTML")["ansible.builtin.set_fact"]
        policy = ENV.from_string(facts["digital_energy_twin_public_csp"]).render(digital_energy_twin_public_connect_origins="")
        directives = {part.split()[0]: part.split()[1:] for part in (p.strip() for p in policy.split(";")) if part}
        # Same-origin bundle and PBKDF2 worker file, challenge from /api/public/altcha/challenge.
        self.assertIn("'self'", directives["script-src"])
        self.assertIn("'self'", directives["worker-src"])
        self.assertIn("'self'", directives["connect-src"])


if __name__ == "__main__":
    unittest.main()
