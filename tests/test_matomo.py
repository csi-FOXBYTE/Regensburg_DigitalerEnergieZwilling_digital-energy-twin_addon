"""Offline Matomo checks; requires Jinja2 and PyYAML (Ansible dependencies)."""

import hashlib
import re
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
ENV.filters["combine"] = lambda *dicts: {key: value for d in dicts for key, value in d.items()}
ENV.filters["hash"] = lambda value, algorithm: hashlib.new(algorithm, value.encode()).hexdigest()
ENV.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
ENV.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None
ENV.tests["skipped"] = lambda result: bool(result.get("skipped"))
ENV.filters["difference"] = lambda values, other: [value for value in values if value not in other]
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
    "digital_energy_twin_matomo_oidc_secret_name": "digital-energy-twin-matomo-oidc",
    "digital_energy_twin_matomo_oidc_client_id": "matomo",
    "digital_energy_twin_matomo_oidc_issuer": "https://idm.example.test/realms/dez",
    "digital_energy_twin_matomo_keycloak_internal_url": "",
    "digital_energy_twin_matomo_client_secret": {"json": {"value": "secret"}},
    "inv_access": {"tenant": {"realm_name": "dez"}},
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
                    if entry["name"].endswith(" when disabled") and module.get("state", "absent") == "absent":
                        # Clean-up runs only when Matomo is switched off, never while it is wanted.
                        self.assertIn("not digital_energy_twin_matomo_requested", " ".join(conditions(entry)))
                    else:
                        self.assertTrue(any("digital_energy_twin_matomo_enabled" in c for c in conditions(entry)))

    def test_disabling_removes_access_and_workloads_but_keeps_data(self):
        removed = [item["kind"] for item in task("Stop Matomo when disabled")["loop"]]
        self.assertEqual(removed, ["Deployment", "CronJob", "StatefulSet"])
        self.assertEqual(task("Remove Matomo route when disabled")["loop"],
                         ["routes/digital-energy-twin-matomo-direct", "upstreams/digital-energy-twin-matomo-upstream"])
        for entry in TASKS:
            if entry["name"].endswith(" when disabled") and entry.get("kubernetes.core.k8s"):
                self.assertNotIn("PersistentVolumeClaim", str(entry))
                self.assertNotIn("Secret", str(entry))

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
        self.assertEqual(env["MATOMO_OIDC_ISSUER"]["value"], "https://idm.example.test/realms/dez")
        self.assertEqual(env["MATOMO_OIDC_CLIENT_ID"]["value"], "matomo")
        self.assertEqual(env["MATOMO_OIDC_CLIENT_SECRET_FILE"]["value"], "/run/secrets/matomo-oidc/client-secret")
        self.assertNotIn("MATOMO_OIDC_BACKCHANNEL_URL", env)
        self.assertNotIn("MATOMO_OIDC_CLIENT_SECRET", env)
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        mounts = {mount["mountPath"]: mount["name"] for mount in container["volumeMounts"]}
        self.assertEqual(mounts["/var/www/html"], "data")
        self.assertEqual(mounts["/run/secrets/matomo-oidc"], "oidc")
        volumes = {volume["name"]: volume for volume in spec["template"]["spec"]["volumes"]}
        self.assertEqual(volumes["data"]["persistentVolumeClaim"]["claimName"], "digital-energy-twin-matomo-data")
        self.assertEqual(volumes["oidc"]["secret"]["secretName"], "digital-energy-twin-matomo-oidc")
        annotations = spec["template"]["metadata"]["annotations"]
        self.assertEqual(annotations["checksum/oidc-client-secret"], hashlib.sha256(b"secret").hexdigest())

    def test_backchannel_url_points_to_the_realm_of_the_internal_keycloak(self):
        deployment = render("matomo_deploy", digital_energy_twin_matomo_keycloak_internal_url="http://keycloak.idm.svc:8080/")
        env = {item["name"]: item for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(env["MATOMO_OIDC_BACKCHANNEL_URL"]["value"], "http://keycloak.idm.svc:8080/realms/dez")

    def test_only_apisix_reaches_matomo(self):
        policy = render("matomo_netpol", digital_energy_twin_apisix_namespace="dev-access-stack")
        self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], {"app": "digital-energy-twin-matomo"})
        self.assertEqual(policy["spec"]["ingress"], [{
            "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "dev-access-stack"}}}],
            "ports": [{"protocol": "TCP", "port": 80}]}])
        names = [entry["name"] for entry in TASKS]
        # The rule exists before the Matomo pod can serve its installer.
        self.assertIn("digital_energy_twin_matomo_netpol.yaml.j2", task("Ensure Matomo database and network rules")["loop"])
        self.assertLess(names.index("Digital Energy Twin: Ensure Matomo database and network rules"),
                        names.index("Digital Energy Twin: Ensure Matomo deployment"))

    def test_client_switches_flows_only_after_verification(self):
        names = [entry["name"].split(": ", 1)[1] for entry in TASKS]
        order = ["Validate Matomo browser flow", "Update Matomo OIDC client and bind its browser flow",
                 "Remove replaced Matomo browser flows"]
        self.assertEqual([names.index(name) for name in order], sorted(names.index(name) for name in order))
        self.assertNotIn("Refuse to replace a Matomo browser flow in use", names)
        # A fresh admin token before the Keycloak tasks, which run after all APISIX tasks.
        self.assertEqual(names.index("Refresh IDM settings for Matomo") + 1, names.index("Set Matomo Keycloak facts"))
        removal = task("Remove replaced Matomo browser flows")
        self.assertIn("rejectattr('id', 'equalto', digital_energy_twin_matomo_flow_id)", removal["loop"])

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
                         "config.ini.php", '-mmin +"$((KEEP_DAYS * 1440))"'):
            self.assertIn(expected, script)
        self.assertEqual(pod["initContainers"][0]["securityContext"]["capabilities"]["add"], ["CHOWN"])

    def test_keycloak_client_is_confidential_standard_flow_only(self):
        facts = task("Set Matomo Keycloak facts")["ansible.builtin.set_fact"]
        settings = facts["digital_energy_twin_matomo_client_settings"]
        self.assertEqual((settings["publicClient"], settings["standardFlowEnabled"], settings["implicitFlowEnabled"],
                          settings["directAccessGrantsEnabled"], settings["serviceAccountsEnabled"]),
                         (False, True, False, False, False))
        host = {"digital_energy_twin_matomo_host": "matomo.det.example.test"}
        self.assertEqual([ENV.from_string(uri).render(host) for uri in settings["redirectUris"]],
                         ["https://matomo.det.example.test/index.php?module=LoginOIDC&action=callback&provider=oidc"])
        self.assertEqual(ENV.from_string(settings["attributes"]["post.logout.redirect.uris"]).render(host),
                         "https://matomo.det.example.test/*")
        roles = {"digital_energy_twin_oidc_client_id": "digital-energy-twin",
                 "digital_energy_twin_matomo_viewer_role": "matomoViewer",
                 "digital_energy_twin_matomo_admin_role": "matomoAdmin"}
        conditions_ = [ENV.from_string(item["role"]).render(roles) for item in facts["digital_energy_twin_matomo_role_conditions"]]
        self.assertEqual(conditions_, ["digital-energy-twin.matomoViewer", "digital-energy-twin.matomoAdmin"])
        self.assertEqual(ENV.from_string(facts["digital_energy_twin_matomo_deny_message"]).render(roles),
                         "Kein Zugriff auf Matomo: Ihrem Konto fehlt die Rolle matomoViewer oder matomoAdmin.")
        defaults = task("Set Matomo defaults")["ansible.builtin.set_fact"]
        self.assertEqual(defaults["digital_energy_twin_matomo_viewer_role"], "matomoViewer")
        self.assertEqual(defaults["digital_energy_twin_matomo_admin_role"], "matomoAdmin")

    def test_role_gate_is_always_checked_with_temporary_users(self):
        check = task("Check the Matomo role gate with temporary users")
        self.assertIn("digital_energy_twin_matomo_enabled", conditions(check))
        names = [entry["name"].split(": ", 1)[1] for entry in check["block"]]
        self.assertIn("Validate that Keycloak denies Matomo without a Matomo role", names)
        self.assertIn("Validate that Keycloak admits Matomo with a Matomo role", names)
        self.assertEqual([entry["name"].split(": ", 1)[1] for entry in check["always"]],
                         ["Delete temporary Matomo check users"])

    def test_role_gate_check_logs_in_with_the_username_keycloak_stored(self):
        check = task("Check the Matomo role gate with temporary users")["block"]
        names = [entry["name"].split(": ", 1)[1] for entry in check]
        self.assertLess(names.index("Read the usernames Keycloak gave the check users"),
                        names.index("Log in as the check users"))
        login = next(entry for entry in check if entry["name"].endswith("Log in as the check users"))
        # Realms with the email as username replace the name the add-on chose.
        self.assertIn(".json.username", login["ansible.builtin.uri"]["body"]["username"])
        guard = next(entry for entry in check
                     if entry["name"].endswith("Validate that the check users got past the login form"))
        expression = ENV.compile_expression(guard["ansible.builtin.assert"]["that"][0].strip())
        login_form = '<form id="kc-form-login" action="https://idm/realms/dez/login-actions/authenticate?x=1">'
        deny_page = ('<p>Kein Zugriff auf Matomo</p>'
                     '<a href="https://idm/realms/dez/login-actions/authenticate?restart=1">Back</a>')
        for results, expected in (
            ([{"status": 302, "content": ""}, {"status": 200, "content": deny_page}], True),
            ([{"status": 200, "content": login_form}, {"status": 302, "content": ""}], False),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(expression(digital_energy_twin_matomo_check_login={"results": results}), expected)

    def test_internal_keycloak_url_is_found_and_used_only_with_the_public_issuer(self):
        choose = task("Choose the internal Keycloak URL to check")["ansible.builtin.set_fact"]
        candidate = ENV.from_string(choose["digital_energy_twin_matomo_keycloak_candidate"])
        services = [
            {"metadata": {"name": "idmkeycloak-headless", "namespace": "cc-loc-access-stack"},
             "spec": {"clusterIP": "None", "ports": [{"name": "http", "port": 8080}]}},
            {"metadata": {"name": "idmkeycloak", "namespace": "cc-loc-access-stack"},
             "spec": {"clusterIP": "10.43.115.238", "ports": [{"name": "http", "port": 80}, {"name": "https", "port": 443}]}},
        ]
        for configured, found, expected in (
            ("", services, "http://idmkeycloak.cc-loc-access-stack.svc.cluster.local"),
            ("", [{"metadata": {"name": "keycloak", "namespace": "access"},
                   "spec": {"clusterIP": "10.0.0.1", "ports": [{"name": "http", "port": 8080}]}}],
             "http://keycloak.access.svc.cluster.local:8080"),
            ("", [], ""),
            ("http://kc.internal:8080/", services, "http://kc.internal:8080"),
        ):
            with self.subTest(configured=configured, services=len(found)):
                self.assertEqual(candidate.render(digital_energy_twin_matomo_keycloak_internal_url=configured,
                                                  digital_energy_twin_access_services={"resources": found}).strip(),
                                 expected)

        decide = task("Decide whether Matomo uses the internal Keycloak URL")
        expression = ENV.from_string(decide["ansible.builtin.set_fact"]["digital_energy_twin_matomo_keycloak_internal_url"])
        issuer = "https://idm.example.test/realms/dez"
        url = "http://idmkeycloak.access.svc.cluster.local"
        for configured, check, found_issuer, expected in (
            ("", {"stdout": "..."}, issuer, url),                    # found, reports the public issuer
            ("", {"stdout": "..."}, "http://idmkeycloak/realms/dez", ""),  # other issuer: public URL instead
            ("", {"skipped": True}, "", ""),                         # nothing to check: public URL
            (url, {"skipped": True}, "", url),                       # set by hand, cannot be checked: kept
            (url, {"stdout": "..."}, "http://other", ""),            # set by hand, wrong issuer: the assert fails
        ):
            with self.subTest(configured=configured, check=check, issuer=found_issuer):
                rendered = expression.render(
                    digital_energy_twin_matomo_keycloak_candidate=url,
                    digital_energy_twin_matomo_keycloak_internal_issuer=found_issuer,
                    digital_energy_twin_matomo_oidc_issuer=issuer,
                    digital_energy_twin_matomo_config={"keycloak_internal_url": configured},
                    digital_energy_twin_matomo_keycloak_issuer_check=check)
                self.assertEqual(rendered.strip(), expected)
        names = [entry["name"] for entry in TASKS]
        self.assertLess(names.index("Digital Energy Twin: Validate the configured internal Keycloak URL"),
                        names.index("Digital Energy Twin: Get Matomo database credentials secret"))

    def test_matomo_checks_keycloak_before_creating_anything(self):
        names = [entry["name"].split(": ", 1)[1] for entry in TASKS]
        first_resource = names.index("Get Matomo database credentials secret")
        for check in ("Validate that users cannot change their username",
                      "Validate that the Matomo flow keeps the realm's second factors",
                      "Validate client scopes for Matomo"):
            self.assertLess(names.index(check), first_resource)
        # The main app is fully deployed before any Matomo step can stop the run.
        self.assertLess(names.index("Upsert backend admin-api route (OIDC protected)"),
                        names.index("Refresh IDM settings for Matomo"))

    def test_misplaced_or_misspelled_matomo_settings_stop_the_run(self):
        placement = task("Validate where the Matomo settings are")["ansible.builtin.assert"]["that"]
        for context, valid in (
            ({"inv_addons": {"digital-energy-twin": {"matomo": {}}}}, True),
            ({"inv_addons": {}, "matomo": {"enable": True}}, False),           # one level too far left
            ({"inv_addons": {"matomo": {"enable": True}}}, False),             # next to the add-on
        ):
            with self.subTest(context=context):
                self.assertEqual(all(ENV.compile_expression(condition)(**context) for condition in placement), valid)

        names = task("Validate the Matomo setting names")
        unknown = ENV.from_string(names["vars"]["digital_energy_twin_matomo_unknown_settings"])
        known = {key: names["vars"][key] for key in ("digital_energy_twin_matomo_known_settings",
                                                       "digital_energy_twin_matomo_known_backup_settings")}
        conditions = names["ansible.builtin.assert"]["that"]
        for matomo, expected_unknown, valid in (
            ({"enable": True}, [], True),
            ({"enable": True, "instaled": True}, ["instaled"], False),
            ({"enable": True, "backup": {"keep_day": 7}}, ["backup.keep_day"], False),
            ({"enable": "true"}, [], False),                                   # a string, not a boolean
            ({"enable": True, "tracking_enabled": False, "site_id": 1, "backup": {"keep_days": 7}}, [], True),
        ):
            with self.subTest(matomo=matomo):
                found = yaml.safe_load(unknown.render(digital_energy_twin={"matomo": matomo}, **known))
                self.assertEqual(found, expected_unknown)
                result = all(ENV.compile_expression(condition)(
                    digital_energy_twin={"matomo": matomo}, digital_energy_twin_matomo_unknown_settings=found)
                    for condition in conditions)
                self.assertEqual(result, valid)

    def test_default_inventory_ships_matomo_switched_off(self):
        defaults = yaml.safe_load((ROOT / "default_inventory.yml").read_text())
        matomo = defaults["all"]["children"]["controller"]["vars"]["inv_addons"]["digital-energy-twin"]["matomo"]
        self.assertEqual(matomo, {"enable": False})

    def test_deployment_waits_for_the_keycloak_client_secret(self):
        names = [entry["name"] for entry in TASKS]
        self.assertLess(names.index("Digital Energy Twin: Store Matomo OIDC client secret in addon namespace"),
                        names.index("Digital Energy Twin: Ensure Matomo deployment"))
        # A Matomo problem must not stop the main app's routes.
        self.assertLess(names.index("Digital Energy Twin: Upsert backend admin-api route (OIDC protected)"),
                        names.index("Digital Energy Twin: Set Matomo Keycloak facts"))

    def test_backend_tracking_stays_off_until_switched_on_after_installation(self):
        tracking = task("Set Matomo defaults")["ansible.builtin.set_fact"]["digital_energy_twin_matomo_tracking"]
        image = NAMES["digital_energy_twin_matomo_image"]
        for config, matomo_image, expected in (
            ({"enable": True, "installed": True}, image, False),
            ({"enable": True, "installed": False, "tracking_enabled": True}, image, False),
            ({"enable": True, "installed": True, "tracking_enabled": True}, "", False),
            ({"enable": False, "installed": True, "tracking_enabled": True}, image, False),
            ({"enable": True, "installed": True, "tracking_enabled": True}, image, True),
        ):
            with self.subTest(config=config, image=bool(matomo_image)):
                result = ENV.from_string(tracking).render(
                    digital_energy_twin_enabled=True, digital_energy_twin_matomo_image=matomo_image,
                    digital_energy_twin_matomo_config=config)
                self.assertEqual(result.strip(), str(expected))
        validation = task("Validate Matomo config")["ansible.builtin.assert"]["that"]
        for matomo, valid in (
            ({"tracking_enabled": True, "installed": True, "site_id": 3}, True),
            ({"tracking_enabled": True, "installed": True}, False),
            ({"tracking_enabled": True, "site_id": 3}, False),
            ({"site_id": "x"}, True),
        ):
            with self.subTest(matomo=matomo):
                checks = [condition for condition in validation if "tracking_enabled" in condition]
                result = all(ENV.compile_expression(condition)(digital_energy_twin={"matomo": matomo})
                             for condition in checks)
                self.assertEqual(result, valid)

        backend = (ROOT / "templates/digital_energy_twin_backend_deploy.yaml.j2").read_text()
        context = {"digital_energy_twin_backend_image": "backend@sha256:" + "b" * 64,
                   "digital_energy_twin_database_secret_name": "db", "digital_energy_twin_backend_port": 5000,
                   "digital_energy_twin_feedback_mail_to": "", "digital_energy_twin_tiles_url": "t",
                   "digital_energy_twin_terrain_url": "t", "digital_energy_twin_address_database_url": "a",
                   "digital_energy_twin_backend_direct_host": "api", "digital_energy_twin_public_direct_host": "det",
                   "digital_energy_twin_admin_direct_host": "admin",
                   "inv_access": {"platform": {"hostname": "https://idm"}, "tenant": {"realm_name": "dez"}},
                   "digital_energy_twin_matomo_host": "matomo.det.example.test", "digital_energy_twin_matomo_site_id": "3"}
        for tracking_on, expected in ((False, {}), (True, {"MATOMO_URL": "https://matomo.det.example.test/matomo.php",
                                                           "MATOMO_SITE_ID": "3"})):
            with self.subTest(tracking=tracking_on):
                deployment = yaml.safe_load(ENV.from_string(backend).render(
                    context, digital_energy_twin_matomo_tracking=tracking_on))
                env = {item["name"]: item.get("value") for item in
                       deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
                self.assertEqual({key: value for key, value in env.items() if key.startswith("MATOMO")}, expected)

    def test_backup_job_starts_only_after_installation(self):
        self.assertIn("digital_energy_twin_matomo_installed", conditions(task("Ensure Matomo backup job")))
        removal = task("Remove Matomo backup job while backups are off")
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
