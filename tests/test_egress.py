"""Offline checks of the egress NetworkPolicies; requires Jinja2 and PyYAML (Ansible dependencies)."""

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")
ENV.filters["combine"] = lambda *dicts: {key: value for d in dicts for key, value in d.items()}
ENV.filters["difference"] = lambda values, other: [value for value in values if value not in other]
ENV.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
ENV.filters["to_json"] = json.dumps
ENV.filters["urlsplit"] = lambda value, part: getattr(urlsplit(value), part)
ENV.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
ENV.tests["search"] = lambda value, pattern: re.search(pattern, value) is not None

TEMPLATE = ENV.from_string((ROOT / "templates/digital_energy_twin_egress_netpol.yaml.j2").read_text())


def service(namespace, name, ports, selector=None, kind="ClusterIP"):
    spec = {"type": kind, "ports": ports}
    if selector is not None:
        spec["selector"] = selector
    return {"metadata": {"namespace": namespace, "name": name}, "spec": spec}


# The services of the local reproduction (k3d), trimmed to what the template reads.
SERVICES = [
    service("default", "kubernetes", [{"port": 443, "targetPort": 6443}]),
    service("cc-loc-database-stack", "central-db", [{"port": 5432, "targetPort": 5432}],
            {"application": "spilo", "cluster-name": "central-db", "spilo-role": "master"}),
    service("cc-loc-mail", "mailpit", [{"name": "smtp", "port": 465, "targetPort": 1025},
                                       {"name": "web", "port": 8025, "targetPort": 8025}], {"app": "mailpit"}),
    service("cc-loc-access-management", "keycloak", [{"name": "http", "port": 80, "targetPort": 8080}], {"app": "keycloak"}),
    service("traefik", "traefik", [{"name": "web", "port": 80, "targetPort": "web", "protocol": "TCP"},
                                   {"name": "websecure", "port": 443, "targetPort": "websecure", "protocol": "TCP"}],
            {"app.kubernetes.io/name": "traefik"}, kind="LoadBalancer"),
]
CONTEXT = {
    "digital_energy_twin_egress": {
        "cluster_cidrs": ["10.42.0.0/16", "10.43.0.0/16"],
        "dns": {"namespace": "kube-system", "pod_labels": {"k8s-app": "kube-dns"}, "port": 53},
        "platform_entry": "auto",
        "extra": {},
    },
    "digital_energy_twin_egress_services": SERVICES,
    "digital_energy_twin_db_mode": "central",
    "digital_energy_twin_db_cluster_name": "central-db",
    "digital_energy_twin_db_host": "central-db.cc-loc-database-stack.svc.cluster.local",
    "digital_energy_twin_db_port": "5432",
    "digital_energy_twin_feedback_mail_to": "feedback@example.test",
    "inv_email": {"server": "mailpit.cc-loc-mail.svc.cluster.local", "port": 465},
    "inv_access": {"platform": {"hostname": "https://idm.example.test"}},
    "digital_energy_twin_matomo_name": "digital-energy-twin-matomo",
    "digital_energy_twin_matomo_db_name": "digital-energy-twin-matomo-db",
    "digital_energy_twin_matomo_backup_name": "digital-energy-twin-matomo-backup",
    "digital_energy_twin_matomo_keycloak_internal_url": "http://keycloak.cc-loc-access-management.svc.cluster.local",
}
DNS_RULE = {
    "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
    "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
}
EXTERNAL = [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.42.0.0/16", "10.43.0.0/16"]}}]
TRAEFIK_RULE = {
    "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "traefik"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}}}],
    "ports": [{"protocol": "TCP", "port": "websecure"}],
}


def render(component, **overrides):
    context = {**CONTEXT, "digital_energy_twin_egress_component": component}
    for key, value in overrides.items():
        if value is None:
            context.pop(key, None)
        else:
            context[key] = value
    return yaml.safe_load(TEMPLATE.render(context))


def egress(component, **overrides):
    return render(component, **overrides)["spec"]["egress"]


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def validate(egress_config):
    expression = task("Validate the egress network policy settings")["vars"]["digital_energy_twin_egress_errors"]
    return ENV.from_string(expression).render(digital_energy_twin={"egress": egress_config}).strip()


class EgressPolicyTests(unittest.TestCase):
    def test_every_component_gets_an_egress_only_policy_for_its_pods(self):
        for component, app in (("backend", "digital-energy-twin-backend"), ("admin", "digital-energy-twin-admin"),
                               ("public", "digital-energy-twin-public"), ("matomo", "digital-energy-twin-matomo"),
                               ("matomo_db", "digital-energy-twin-matomo-db"),
                               ("matomo_backup", "digital-energy-twin-matomo-backup")):
            with self.subTest(component=component):
                policy = render(component)
                self.assertEqual(policy["kind"], "NetworkPolicy")
                self.assertEqual(policy["metadata"]["name"], app + "-egress")
                self.assertEqual(policy["metadata"]["labels"]["digital-energy-twin/policy"], "egress")
                self.assertEqual(policy["spec"]["podSelector"], {"matchLabels": {"app": app}})
                # Egress only: the ingress policies stay as they are.
                self.assertEqual(policy["spec"]["policyTypes"], ["Egress"])
                self.assertEqual(policy["spec"]["egress"][0], DNS_RULE)

    def test_frontends_and_the_matomo_database_only_resolve_names(self):
        for component in ("admin", "public", "matomo_db"):
            with self.subTest(component=component):
                self.assertEqual(egress(component), [DNS_RULE])

    def test_backend_reaches_its_database_smtp_and_https_outside_the_cluster(self):
        rules = egress("backend")
        self.assertEqual(rules, [
            DNS_RULE,
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "cc-loc-database-stack"}},
                     "podSelector": {"matchLabels": {"application": "spilo", "cluster-name": "central-db",
                                                     "spilo-role": "master"}}}],
             "ports": [{"protocol": "TCP", "port": 5432}]},
            # The SMTP service port 465 leads to container port 1025.
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "cc-loc-mail"}},
                     "podSelector": {"matchLabels": {"app": "mailpit"}}}],
             "ports": [{"protocol": "TCP", "port": 1025}]},
            {"to": EXTERNAL, "ports": [{"protocol": "TCP", "port": 443}]},
            TRAEFIK_RULE,
        ])

    def test_nothing_opens_the_kubernetes_api_or_whole_cluster_networks(self):
        for component in ("backend", "admin", "public", "matomo", "matomo_db", "matomo_backup"):
            for rule in egress(component):
                for peer in rule["to"]:
                    with self.subTest(component=component, peer=peer):
                        self.assertNotIn("default", json.dumps(peer.get("namespaceSelector", {})))
                        if "ipBlock" in peer:
                            self.assertEqual(peer["ipBlock"]["except"], ["10.42.0.0/16", "10.43.0.0/16"])
                        elif "namespaceSelector" not in peer:
                            # Only the Matomo database is addressed in the own namespace.
                            self.assertEqual(peer, {"podSelector": {"matchLabels": {"app": "digital-energy-twin-matomo-db"}}})
                    for port in rule["ports"]:
                        self.assertNotIn(port["port"], (6443, 2379, 9180))

    def test_keycloak_port_of_the_public_url_is_allowed(self):
        rules = egress("backend", inv_access={"platform": {"hostname": "https://idm.example.test:8443"}})
        external = [rule for rule in rules if rule["to"] == EXTERNAL]
        self.assertEqual(external[0]["ports"], [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 8443}])

    def test_zalando_master_service_without_selector_falls_back_to_spilo_labels(self):
        services = [s for s in SERVICES if s["metadata"]["name"] != "central-db"]
        services.append(service("dbs", "energy-db", [{"port": 5432, "targetPort": 5432}]))
        rules = egress("backend", digital_energy_twin_egress_services=services, digital_energy_twin_db_mode="zalando",
                       digital_energy_twin_db_cluster_name="energy-db",
                       digital_energy_twin_db_host="energy-db.dbs.svc.cluster.local")
        self.assertEqual(rules[1], {
            "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "dbs"}},
                    "podSelector": {"matchLabels": {"application": "spilo", "cluster-name": "energy-db"}}}],
            "ports": [{"protocol": "TCP", "port": 5432}]})

    def test_existing_database_outside_the_cluster_uses_the_external_rule_on_its_port(self):
        rules = egress("backend", digital_energy_twin_db_mode="existing", digital_energy_twin_db_cluster_name=None,
                       digital_energy_twin_db_host="db.example.org", digital_energy_twin_db_port="6432")
        self.assertEqual(rules[1], {"to": EXTERNAL, "ports": [{"protocol": "TCP", "port": 6432}]})

    def test_existing_database_service_that_is_not_found_allows_only_its_namespace(self):
        rules = egress("backend", digital_energy_twin_db_mode="existing", digital_energy_twin_db_cluster_name=None,
                       digital_energy_twin_db_host="postgres.dev-databases.svc")
        self.assertEqual(rules[1], {
            "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "dev-databases"}}}],
            "ports": [{"protocol": "TCP", "port": 5432}]})

    def test_external_smtp_server_and_no_mail_without_feedback_address(self):
        rules = egress("backend", inv_email={"server": "smtp.example.org", "port": 587})
        self.assertIn({"to": EXTERNAL, "ports": [{"protocol": "TCP", "port": 587}]}, rules)
        rules = egress("backend", digital_energy_twin_feedback_mail_to="")
        self.assertFalse(any("mailpit" in json.dumps(rule) for rule in rules))
        self.assertEqual(len(rules), 4)

    def test_matomo_reaches_its_database_and_the_internal_keycloak_only(self):
        self.assertEqual(egress("matomo"), [
            DNS_RULE,
            {"to": [{"podSelector": {"matchLabels": {"app": "digital-energy-twin-matomo-db"}}}],
             "ports": [{"protocol": "TCP", "port": 3306}]},
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "cc-loc-access-management"}},
                     "podSelector": {"matchLabels": {"app": "keycloak"}}}],
             "ports": [{"protocol": "TCP", "port": 8080}]},
        ])

    def test_matomo_without_internal_keycloak_calls_the_public_keycloak(self):
        rules = egress("matomo", digital_energy_twin_matomo_keycloak_internal_url="")
        self.assertEqual(rules[2:], [{"to": EXTERNAL, "ports": [{"protocol": "TCP", "port": 443}]}, TRAEFIK_RULE])

    def test_backup_reaches_only_the_matomo_database(self):
        self.assertEqual(egress("matomo_backup"), [
            DNS_RULE,
            {"to": [{"podSelector": {"matchLabels": {"app": "digital-energy-twin-matomo-db"}}}],
             "ports": [{"protocol": "TCP", "port": 3306}]},
        ])

    def test_configured_platform_entry_replaces_the_loadbalancer_detection(self):
        config = {**CONTEXT["digital_energy_twin_egress"],
                  "platform_entry": [{"namespace": "ingress-nginx", "pod_labels": {"app": "ingress-nginx"}, "ports": ["443"]},
                                     {"namespace": "edge", "ports": ["https"]}]}
        rules = egress("backend", digital_energy_twin_egress=config)
        self.assertNotIn(TRAEFIK_RULE, rules)
        self.assertEqual(rules[-2:], [
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}},
                     "podSelector": {"matchLabels": {"app": "ingress-nginx"}}}],
             "ports": [{"protocol": "TCP", "port": 443}]},
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "edge"}}}],
             "ports": [{"protocol": "TCP", "port": "https"}]},
        ])
        self.assertEqual(egress("backend", digital_energy_twin_egress={**config, "platform_entry": []})[-1]["to"], EXTERNAL)

    def test_extra_destinations_and_ipv6_cluster_networks(self):
        config = {**CONTEXT["digital_energy_twin_egress"],
                  "cluster_cidrs": ["10.42.0.0/16", "fd00:42::/56"],
                  "extra": {"public": [{"cidr": "192.0.2.10/32", "ports": [8443]},
                                       {"namespace": "monitoring", "pod_labels": {"app": "otel"}, "ports": [4317],
                                        "protocol": "TCP"}]}}
        self.assertEqual(egress("public", digital_energy_twin_egress=config), [
            DNS_RULE,
            {"to": [{"ipBlock": {"cidr": "192.0.2.10/32"}}], "ports": [{"protocol": "TCP", "port": 8443}]},
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                     "podSelector": {"matchLabels": {"app": "otel"}}}], "ports": [{"protocol": "TCP", "port": 4317}]},
        ])
        external = egress("backend", digital_energy_twin_egress=config)[-2]["to"]
        self.assertEqual(external, [{"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.42.0.0/16"]}},
                                    {"ipBlock": {"cidr": "::/0", "except": ["fd00:42::/56"]}}])


class EgressSettingsTests(unittest.TestCase):
    def test_defaults_and_valid_settings_pass(self):
        self.assertEqual(validate({}), "")
        self.assertEqual(validate({
            "enable": False,
            "cluster_cidrs": ["10.244.0.0/16", "10.96.0.0/12", "fd00:10:96::/112"],
            "dns": {"namespace": "kube-system", "pod_labels": {"k8s-app": "kube-dns"}, "port": 53},
            "platform_entry": [{"namespace": "ingress-nginx", "pod_labels": {"app.kubernetes.io/name": "ingress-nginx"},
                                "ports": [443, "https"]}],
            "extra": {"backend": [{"cidr": "192.0.2.0/24", "ports": [5432]}],
                      "matomo": [{"namespace": "mail", "ports": [25], "protocol": "TCP"}]},
        }), "")

    def test_defaults_match_the_k3s_networks_and_coredns(self):
        defaults = task("Set egress network policy defaults")["ansible.builtin.set_fact"]
        self.assertIn("default(true)", defaults["digital_energy_twin_egress_enabled"])
        self.assertIn("['10.42.0.0/16', '10.43.0.0/16']", defaults["digital_energy_twin_egress"]["cluster_cidrs"])
        self.assertIn("'k8s-app': 'kube-dns'", defaults["digital_energy_twin_egress"]["dns"])
        self.assertIn("default('auto')", defaults["digital_energy_twin_egress"]["platform_entry"])

    def test_invalid_settings_are_refused(self):
        cases = {
            "unknown": {"cluster_cidr": ["10.0.0.0/8"]},
            "quoted enable": {"enable": "false"},
            "empty networks": {"cluster_cidrs": []},
            "string networks": {"cluster_cidrs": "10.42.0.0/16"},
            "no prefix": {"cluster_cidrs": ["10.42.0.0"]},
            "host bits": {"cluster_cidrs": ["10.42.0.1/16"]},
            "everything": {"cluster_cidrs": ["0.0.0.0/0"]},
            "dns port": {"dns": {"port": 0}},
            "dns labels": {"dns": {"pod_labels": {"k8s-app": 1}}},
            "dns namespace": {"dns": {"namespace": "Kube_System"}},
            "entry string": {"platform_entry": "ingress-nginx"},
            "entry without ports": {"platform_entry": [{"namespace": "ingress-nginx"}]},
            "entry port": {"platform_entry": [{"namespace": "ingress-nginx", "ports": [70000]}]},
            "extra component": {"extra": {"frontend": []}},
            "extra both": {"extra": {"backend": [{"cidr": "192.0.2.0/24", "namespace": "x", "ports": [1]}]}},
            "extra neither": {"extra": {"backend": [{"ports": [1]}]}},
            "extra labels on cidr": {"extra": {"backend": [{"cidr": "192.0.2.0/24", "pod_labels": {"a": "b"}, "ports": [1]}]}},
            "extra port name": {"extra": {"backend": [{"cidr": "192.0.2.0/24", "ports": ["https"]}]}},
            "extra protocol": {"extra": {"backend": [{"cidr": "192.0.2.0/24", "ports": [1], "protocol": "ICMP"}]}},
            "extra not a list": {"extra": {"backend": {"cidr": "192.0.2.0/24", "ports": [1]}}},
        }
        for name, config in cases.items():
            with self.subTest(name=name):
                self.assertNotEqual(validate(config), "")


class EgressTaskTests(unittest.TestCase):
    def test_policies_are_applied_before_the_workloads_and_removed_when_switched_off(self):
        names = [entry["name"] for entry in TASKS]
        app = names.index("Digital Energy Twin: Ensure egress network policies for the app workloads")
        self.assertLess(app, names.index("Digital Energy Twin: Ensure backend deployment"))
        matomo = names.index("Digital Energy Twin: Ensure Matomo egress network policies")
        self.assertLess(names.index("Digital Energy Twin: Decide whether Matomo uses the internal Keycloak URL"), matomo)
        self.assertLess(matomo, names.index("Digital Energy Twin: Ensure Matomo database and network rules"))
        self.assertEqual(task("Ensure egress network policies for the app workloads")["loop"], ["backend", "admin", "public"])
        self.assertEqual(task("Ensure Matomo egress network policies")["loop"], ["matomo_db", "matomo_backup", "matomo"])
        removal = task("Remove egress network policies when switched off")
        self.assertEqual(removal["kubernetes.core.k8s"]["state"], "absent")
        self.assertIn("not digital_energy_twin_egress_enabled", removal["when"])
        self.assertEqual(len(removal["loop"]), 6)


if __name__ == "__main__":
    unittest.main()
