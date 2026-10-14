"""Offline Matomo checks; requires Jinja2 and PyYAML (Ansible dependencies)."""

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
ENV.filters["combine"] = lambda *dicts: {key: value for d in dicts for key, value in d.items()}
NAMES = {
    "digital_energy_twin_matomo_name": "digital-energy-twin-matomo",
    "digital_energy_twin_matomo_db_name": "digital-energy-twin-matomo-db",
    "digital_energy_twin_matomo_backup_name": "digital-energy-twin-matomo-backup",
    "digital_energy_twin_matomo_db_secret_name": "digital-energy-twin-matomo-db",
    "digital_energy_twin_matomo_data_pvc_name": "digital-energy-twin-matomo-data",
    "digital_energy_twin_matomo_db_database": "matomo",
    "digital_energy_twin_matomo_db_user": "matomo",
    "digital_energy_twin_matomo_db_image": "docker.io/library/mariadb:11.8.9@sha256:" + "d" * 64,
    "digital_energy_twin_matomo_image": "ghcr.io/csi-foxbyte/regensburg-web-matomo@sha256:" + "a" * 64,
    "digital_energy_twin_matomo_service_name": "matomo-det-example-test",
    "digital_energy_twin_matomo_backup_schedule": "30 2 * * *",
    "digital_energy_twin_matomo_backup_keep_days": 14,
}


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def render(name, **context):
    template = ENV.from_string((ROOT / f"templates/digital_energy_twin_{name}.yaml.j2").read_text())
    return yaml.safe_load(template.render({**NAMES, **context}))


def conditions(entry):
    when = entry.get("when", [])
    return [when] if isinstance(when, str) else when


class MatomoTests(unittest.TestCase):
    def test_every_matomo_resource_task_needs_matomo_and_its_image(self):
        enabled = task("Set Matomo defaults")["ansible.builtin.set_fact"]["digital_energy_twin_matomo_enabled"]
        for image, expected in (("", False), (NAMES["digital_energy_twin_matomo_image"], True)):
            with self.subTest(image=image):
                result = ENV.from_string(enabled).render(
                    digital_energy_twin_enabled=True, digital_energy_twin_matomo_image=image,
                    digital_energy_twin_matomo_config={"enable": True})
                self.assertEqual(result, str(expected))
        for entry in TASKS:
            module = entry.get("kubernetes.core.k8s") or entry.get("ansible.builtin.uri") or {}
            if "matomo" in str(module).lower():
                with self.subTest(task=entry["name"]):
                    self.assertTrue(any("digital_energy_twin_matomo_enabled" in c for c in conditions(entry)))

    def test_deployment_uses_the_digest_and_keeps_the_trusted_host_check(self):
        deployment = render("matomo_deploy")
        spec = deployment["spec"]
        self.assertEqual(spec["strategy"], {"type": "Recreate"})
        container = spec["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], NAMES["digital_energy_twin_matomo_image"])
        env = {item["name"]: item for item in container["env"]}
        self.assertNotIn("MATOMO_DISABLE_TRUSTED_HOST_CHECK", env)
        self.assertEqual(env["MATOMO_BEHIND_TLS_PROXY"]["value"], "1")
        self.assertEqual(env["MATOMO_DATABASE_ADAPTER"]["value"], "mysql")
        self.assertEqual(env["MATOMO_DATABASE_TABLES_PREFIX"]["value"], "matomo_")
        self.assertEqual(env["MATOMO_DATABASE_PASSWORD"]["valueFrom"]["secretKeyRef"],
                         {"name": "digital-energy-twin-matomo-db", "key": "password"})
        self.assertEqual(container["ports"], [{"containerPort": 80}])
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        mounts = {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}
        self.assertEqual(mounts["/var/www/html"], "data")
        volumes = {volume["name"]: volume for volume in spec["template"]["spec"]["volumes"]}
        self.assertEqual(volumes["data"]["persistentVolumeClaim"]["claimName"], "digital-energy-twin-matomo-data")

    def test_only_matomo_and_its_backup_reach_the_database(self):
        policy = render("matomo_db_netpol")
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], {"app": "digital-energy-twin-matomo-db"})
        sources = [peer["podSelector"]["matchLabels"]["app"] for peer in policy["spec"]["ingress"][0]["from"]]
        self.assertEqual(sources, ["digital-energy-twin-matomo", "digital-energy-twin-matomo-backup"])
        database = render("matomo_db_statefulset")["spec"]["template"]["spec"]
        self.assertTrue(database["securityContext"]["runAsNonRoot"])
        self.assertEqual(database["volumes"][0]["persistentVolumeClaim"]["claimName"],
                         "digital-energy-twin-matomo-db-data")

    def test_volumes_are_read_write_once_with_optional_storage_class(self):
        for storage_class in ("", "longhorn"):
            with self.subTest(storage_class=storage_class):
                claim = render("matomo_pvc", digital_energy_twin_matomo_storage_class=storage_class,
                               digital_energy_twin_matomo_pvc={"name": "n", "app": "a", "size": "5Gi"})
                self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteOnce"])
                self.assertEqual(claim["spec"].get("storageClassName"), storage_class or None)

    def test_backup_reads_only_the_config_directory_on_the_matomo_node(self):
        job = render("matomo_backup_cronjob")
        self.assertEqual(job["spec"]["concurrencyPolicy"], "Forbid")
        pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertNotIn("fsGroup", pod["securityContext"])
        affinity = pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]
        self.assertEqual(affinity["labelSelector"]["matchLabels"], {"app": "digital-energy-twin-matomo"})
        self.assertEqual(affinity["topologyKey"], "kubernetes.io/hostname")
        backup = pod["containers"][0]
        self.assertTrue(backup["securityContext"]["runAsNonRoot"])
        mounts = {mount["name"]: mount for mount in backup["volumeMounts"]}
        self.assertEqual(mounts["matomo-config"], {"name": "matomo-config", "mountPath": "/matomo-config",
                                                   "subPath": "config", "readOnly": True})
        script = backup["args"][0]
        for expected in ("set -euo pipefail", "--single-transaction", "--routines",
                         "config.ini.php", '-mtime +"$KEEP_DAYS"'):
            self.assertIn(expected, script)
        self.assertEqual(pod["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])

    def test_backup_job_starts_only_after_installation(self):
        self.assertIn("digital_energy_twin_matomo_installed", conditions(task("Ensure Matomo backup job")))
        removal = task("Remove Matomo backup job when disabled")
        self.assertEqual(removal["kubernetes.core.k8s"]["state"], "absent")

    def test_route_admits_only_allowed_networks_until_installed(self):
        route = task("Upsert Matomo direct-host route")
        expression = route["ansible.builtin.uri"]["body"]["plugins"].strip()[2:-2]
        context = {"digital_energy_twin_matomo_install_lock": {"ip-restriction": {}},
                   "digital_energy_twin_matomo_real_ip": {"real-ip": {}},
                   "digital_energy_twin_matomo_deny_all": {"serverless-pre-function": {}}}
        evaluate = ENV.compile_expression(expression)
        for installed, cidrs, proxies, expected in (
            (True, ["192.0.2.10/32"], [], {}),
            (False, ["192.0.2.10/32"], [], {"ip-restriction": {}}),
            (False, ["192.0.2.10/32"], ["10.0.0.0/8"], {"ip-restriction": {}, "real-ip": {}}),
            (False, [], [], {"serverless-pre-function": {}}),
        ):
            with self.subTest(installed=installed, cidrs=cidrs, proxies=proxies):
                self.assertEqual(evaluate(**context, digital_energy_twin_matomo_installed=installed,
                                          digital_energy_twin_matomo_install_allowed_cidrs=cidrs,
                                          digital_energy_twin_matomo_install_trusted_proxies=proxies), expected)
        self.assertEqual(route["vars"]["digital_energy_twin_matomo_install_lock"]["ip-restriction"]["whitelist"],
                         "{{ digital_energy_twin_matomo_install_allowed_cidrs }}")


if __name__ == "__main__":
    unittest.main()
