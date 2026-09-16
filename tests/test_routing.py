"""Offline routing checks; requires Jinja2 and PyYAML (Ansible dependencies)."""

import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
ENV = Environment(undefined=StrictUndefined)
ENV.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1", "on")


def task(suffix):
    return next(entry for entry in TASKS if entry["name"].endswith(": " + suffix))


def active(entry, context):
    conditions = entry.get("when", [])
    if isinstance(conditions, str):
        conditions = [conditions]
    return all(ENV.compile_expression(condition)(**context) for condition in conditions)


def context(mode, http=False):
    return {
        "digital_energy_twin_routing_mode": mode,
        "digital_energy_twin_enabled": True,
        "digital_energy_twin_apisix_namespace": "dev-access-stack",
        "digital_energy_twin_apisix_service_name": "apisix-dev-access-stack-gateway",
        # Only the selected mode's class exists, as on an Ingress-only platform.
        "inv_k8s": {
            "gateway_class" if mode == "gateway" else "ingress_class":
                "traefik" if mode == "gateway" else "nginx",
            "ingress": {"http": http},
            "cert_manager": {"issuer_name": "platform-ca"},
        },
        "item": "addons/digital-energy-twin_addon/tasks.yml",
    }


class RoutingTests(unittest.TestCase):
    def test_explicit_selection_and_gateway_default(self):
        selection = task("Select routing mode")["ansible.builtin.set_fact"]
        expression = ENV.from_string(selection["digital_energy_twin_routing_mode"])
        self.assertEqual(expression.render(digital_energy_twin={}), "gateway")
        validation = task("Validate routing mode")["ansible.builtin.assert"]["that"][0]
        for mode in ("gateway", "ingress", "auto", "", None):
            with self.subTest(mode=mode):
                valid = ENV.compile_expression(validation)(digital_energy_twin_routing_mode=mode)
                self.assertEqual(valid, mode in ("gateway", "ingress"))

    def test_ingress_mode_never_requires_or_touches_gateway_api(self):
        ctx = context("ingress", http=True)
        for suffix in (
            "Validate Gateway API config", "Discover Gateway API resources",
            "Validate Gateway API availability", "Ensure Gateway API routing for addon hosts",
            "Remove Gateway redirects when HTTP is enabled",
        ):
            with self.subTest(task=suffix):
                self.assertFalse(active(task(suffix), ctx))
        self.assertTrue(active(task("Validate Ingress config"), ctx))
        self.assertTrue(active(task("Ensure Ingress routing for addon hosts"), ctx))

    def test_gateway_mode_never_requires_or_creates_ingress(self):
        ctx = context("gateway")
        self.assertFalse(active(task("Validate Ingress config"), ctx))
        self.assertFalse(active(task("Ensure Ingress routing for addon hosts"), ctx))
        self.assertTrue(active(task("Discover Gateway API resources"), ctx))
        self.assertTrue(active(task("Ensure Gateway API routing for addon hosts"), ctx))
        for available in (True, False):
            ctx["digital_energy_twin_gateway_api_result"] = {"api_found": available}
            condition = task("Validate Gateway API availability")["ansible.builtin.assert"]["that"][0]
            self.assertEqual(ENV.compile_expression(condition)(**ctx), available)

    def test_both_modes_keep_all_hosts_behind_apisix_with_tls(self):
        for mode in ("gateway", "ingress"):
            template = ENV.from_string((ROOT / f"templates/digital_energy_twin_{mode}.yaml.j2").read_text())
            for role, hostname in (
                ("admin", "admin.det.example.test"),
                ("public", "det.example.test"),
                ("backend", "api.det.example.test"),
            ):
                for http in (False, True, "false", "true"):
                    with self.subTest(mode=mode, role=role, http=http):
                        ctx = context(mode, http)
                        name = f"digital-energy-twin-{role}-host"
                        ctx[f"digital_energy_twin_{mode}_host"] = {"name": name, "hostname": hostname}
                        resources = list(yaml.safe_load_all(template.render(ctx)))
                        allow_http = ENV.filters["bool"](http)
                        for resource in resources:
                            self.assertEqual(resource["metadata"]["namespace"], "dev-access-stack")
                        if mode == "ingress":
                            self.assertEqual(len(resources), 1)
                            ingress = resources[0]
                            self.assertEqual(ingress["kind"], "Ingress")
                            self.assertEqual(ingress["spec"]["ingressClassName"], "nginx")
                            self.assertEqual(ingress["spec"]["tls"], [{"hosts": [hostname], "secretName": hostname + "-tls"}])
                            self.assertEqual(ingress["spec"]["rules"][0]["host"], hostname)
                            backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
                            self.assertEqual(backend, {"name": ctx["digital_energy_twin_apisix_service_name"], "port": {"number": 80}})
                            self.assertEqual(ingress["metadata"]["annotations"]["nginx.ingress.kubernetes.io/ssl-redirect"], str(not allow_http).lower())
                        else:
                            self.assertEqual(len(resources), 2 if allow_http else 3)
                            gateway, route = resources[0], resources[-1]
                            self.assertEqual(gateway["kind"], "Gateway")
                            self.assertEqual(gateway["spec"]["gatewayClassName"], "traefik")
                            self.assertEqual(gateway["spec"]["listeners"][1]["tls"]["certificateRefs"], [{"name": hostname + "-tls"}])
                            self.assertEqual(route["spec"]["hostnames"], [hostname])
                            self.assertEqual(route["spec"]["rules"][0]["backendRefs"], [{"name": ctx["digital_energy_twin_apisix_service_name"], "port": 80}])
                            self.assertEqual([ref["sectionName"] for ref in route["spec"]["parentRefs"]], ["web", "websecure"] if allow_http else ["websecure"])
                            if not allow_http:
                                redirect = resources[1]["spec"]["rules"][0]["filters"][0]
                                self.assertEqual(redirect, {"type": "RequestRedirect", "requestRedirect": {"scheme": "https", "statusCode": 301}})

    def test_route_loops_preserve_outer_addon_loop_and_respect_disabled_addon(self):
        for suffix in (
            "Discover Gateway API resources", "Validate Gateway API availability",
            "Ensure Gateway API routing for addon hosts", "Ensure Ingress routing for addon hosts",
            "Remove Gateway redirects when HTTP is enabled",
        ):
            entry = task(suffix)
            self.assertNotEqual(entry["loop_control"]["loop_var"], "item")
            for mode in ("gateway", "ingress"):
                ctx = context(mode, http=True)
                ctx["digital_energy_twin_enabled"] = False
                self.assertFalse(active(entry, ctx))
        cleanup = task("Remove Gateway redirects when HTTP is enabled")
        self.assertFalse(active(cleanup, context("gateway", http=False)))
        self.assertTrue(active(cleanup, context("gateway", http=True)))


if __name__ == "__main__":
    unittest.main()
