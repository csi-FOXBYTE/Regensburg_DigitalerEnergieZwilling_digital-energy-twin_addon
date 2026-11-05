"""Offline checks of the backend pod's security contexts; requires Jinja2 and PyYAML."""

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).strip().lower() in ("yes", "on", "1", "true")


def render_pod(**overrides):
    template = ENV.from_string((ROOT / "templates/digital_energy_twin_backend_deploy.yaml.j2").read_text())
    deployment = yaml.safe_load(template.render({
        "digital_energy_twin_backend_image": "backend@sha256:" + "b" * 64,
        "digital_energy_twin_database_secret_name": "db", "digital_energy_twin_altcha_secret_name": "altcha",
        "digital_energy_twin_backend_port": 5000, "digital_energy_twin_submission_deletion_token_ttl_days": 30,
        "digital_energy_twin_feedback_mail_to": "", "digital_energy_twin_tiles_url": "t",
        "digital_energy_twin_terrain_url": "t", "digital_energy_twin_address_database_url": "a",
        "digital_energy_twin_backend_direct_host": "api", "digital_energy_twin_public_direct_host": "det",
        "digital_energy_twin_admin_direct_host": "admin", "digital_energy_twin_oidc_client_id": "dez",
        "digital_energy_twin_backend_cors_additional_origins": "",
        "inv_access": {"platform": {"hostname": "https://idm"}, "tenant": {"realm_name": "dez"}},
        "digital_energy_twin_smtp_secret_name": "smtp", "inv_email": {"email_from": "noreply@example.test"},
        **overrides,
    }))
    return deployment["spec"]["template"]["spec"]


class BackendSecurityContextTest(unittest.TestCase):
    def test_all_containers_run_non_root_with_a_read_only_root_filesystem(self):
        pod = render_pod()
        for container in pod["initContainers"] + pod["containers"]:
            with self.subTest(container=container["name"]):
                context = container["securityContext"]
                self.assertTrue(context["runAsNonRoot"])
                self.assertEqual(context["runAsUser"], 1000)
                self.assertFalse(context["allowPrivilegeEscalation"])
                self.assertTrue(context["readOnlyRootFilesystem"])
                self.assertEqual(context["capabilities"], {"drop": ["ALL"]})

    def test_migration_writes_only_to_its_own_size_limited_tmp(self):
        pod = render_pod()
        migrate = next(container for container in pod["initContainers"] if container["name"] == "migrate")
        self.assertEqual(migrate["volumeMounts"], [{"name": "migrate-tmp", "mountPath": "/tmp"}])
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual(volumes["migrate-tmp"]["emptyDir"], {"sizeLimit": "128Mi"})
        # The backend keeps its own /tmp; the downloaded engine does not reach it.
        backend = pod["containers"][0]
        self.assertNotIn("migrate-tmp", [mount["name"] for mount in backend["volumeMounts"]])

        env = {item["name"]: item.get("value") for item in migrate["env"]}
        self.assertEqual(env["HOME"], "/tmp")
        self.assertEqual(env["CHECKPOINT_DISABLE"], "1")

        self.assertEqual(migrate["command"][:2], ["/bin/sh", "-c"])
        script = migrate["command"][2]
        self.assertIn("cp -R src/zenstack /tmp/zenstack", script)
        self.assertIn('"schema-engine": "/tmp/prisma-engines"', script)
        self.assertIn("export PRISMA_SCHEMA_ENGINE_BINARY", script)
        self.assertTrue(script.rstrip().endswith(
            "exec node_modules/.bin/zenstack migrate deploy --schema /tmp/zenstack/schema.zmodel"))


if __name__ == "__main__":
    unittest.main()


class BackendMailEnvTest(unittest.TestCase):
    def test_feedback_mail_uses_the_variables_the_backend_reads(self):
        backend = next(c for c in render_pod(digital_energy_twin_feedback_mail_to="feedback@example.test")["containers"]
                       if c["name"] == "backend")
        env = {e["name"]: e.get("value") for e in backend["env"]}
        # apps/backend/src/feedback/feedback.service.ts reads MAIL_FROM and FEEDBACK_MAIL_TO.
        self.assertEqual(env["MAIL_FROM"], "noreply@example.test")
        self.assertEqual(env["FEEDBACK_MAIL_TO"], "feedback@example.test")
        self.assertIn("SMTP_URL", env)
        self.assertNotIn("FEEDBACK_MAIL_FROM", env)
