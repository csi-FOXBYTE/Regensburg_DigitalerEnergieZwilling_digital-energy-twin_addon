"""Offline checks for selecting one immutable monorepo application release."""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

from scripts.adopt_application_release import COMPONENTS, prepare_release


ROOT = Path(__file__).resolve().parents[1]
# The tag-based selection used before the first adopted release (removed by v1.7.3).
SOFTWARE_REFERENCES = """---
software:
  digital-energy-twin:
    admin:
      registry: "ghcr.io"
      repository: "csi-foxbyte/regensburg_digitalerenergiezwilling_adminfrontend"
      tag: "0.11.1"
    public:
      registry: "ghcr.io"
      repository: "csi-foxbyte/regensburg_digitalerenergiezwilling_frontend"
      tag: "0.19.0"
    backend:
      registry: "ghcr.io"
      repository: "csi-foxbyte/regensburg_digitalerenergiezwilling_backend"
      tag: "0.9.0"
"""


def task_defaults():
    tasks = yaml.safe_load((ROOT / "tasks/digital-energy-twin.yml").read_text())
    return next(task["set_fact"] for task in tasks if task["name"].endswith(": Set defaults"))


class ReleaseAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "addon"
        (self.source / "release/sboms").mkdir(parents=True)
        (self.destination / "vars").mkdir(parents=True)
        shutil.copy(ROOT / "sbom.config.json", self.destination / "sbom.config.json")
        (self.destination / "vars/software_references.yml").write_text(SOFTWARE_REFERENCES)
        self.manifest = {
            "schema": 2, "appVersion": "1.7.3", "sourceCommit": "a" * 40,
            "runId": "456", "sboms": {"sourceCommit": "a" * 40, "images": {}, "files": {}},
        }
        images = {}
        for index, (destination_name, (component, package_name)) in enumerate(COMPONENTS.items()):
            image = {
                "image": f"ghcr.io/csi-foxbyte/regensburg-web-{component}",
                "digest": f"sha256:{str(index + 1) * 64}",
                "build": "456-1", "sourceCommit": "b" * 40,
            }
            self.manifest[component] = image
            self.manifest["sboms"]["images"][component] = {
                "digest": image["digest"], "sourceCommit": image["sourceCommit"],
                "build": image["build"],
            }
            images[component] = f"{image['image']}@{image['digest']}"
            bom = {
                "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
                "serialNumber": f"urn:uuid:00000000-0000-4000-8000-00000000000{index}",
                "metadata": {
                    "component": {"type": "application", "name": package_name,
                                  "version": "build-456-1", "bom-ref": package_name,
                                  "properties": [{"name": "vcs:commit", "value": "b" * 40}]},
                    "properties": [
                        {"name": "regensburg:release-source", "value": "a" * 40},
                        {"name": "regensburg:image-digest", "value": image["digest"]},
                    ],
                },
            }
            filename = f"{component}.cdx.json"
            content = (json.dumps(bom) + "\n").encode()
            (self.source / "release/sboms" / filename).write_bytes(content)
            self.manifest["sboms"]["files"][filename] = "sha256:" + hashlib.sha256(content).hexdigest()
        (self.source / "release/manifest.json").write_text(json.dumps(self.manifest))
        self.release_file = self.root / "release.json"
        self.release_file.write_text(json.dumps({"tag_name": "v1.7.3", "draft": False}))
        self.selection_file = self.root / "selection.json"
        self.selection_file.write_text(json.dumps({"appVersion": "1.7.3", "sourceCommit": "a" * 40,
                                                   "images": images}))

    def remove_matomo_from_release(self):
        """Turn the fixture into a release from before matomo was added (up to v1.7.3)."""
        del self.manifest["matomo"]
        del self.manifest["sboms"]["images"]["matomo"]
        del self.manifest["sboms"]["files"]["matomo.cdx.json"]
        (self.source / "release/sboms/matomo.cdx.json").unlink()
        (self.source / "release/manifest.json").write_text(json.dumps(self.manifest))
        selection = json.loads(self.selection_file.read_text())
        del selection["images"]["matomo"]
        self.selection_file.write_text(json.dumps(selection))

    def adopt(self):
        return prepare_release(self.source, self.release_file, self.selection_file,
                               "v1.7.3", self.destination)

    def test_adopts_one_manifest_and_pins_all_images_by_digest(self):
        self.adopt()
        selected = json.loads((self.destination / "vars/application_release.json").read_text())[
            "digital_energy_twin_application_release"]
        self.assertEqual(selected["manifest"], self.manifest)
        self.assertFalse((self.destination / "vars/software_references.yml").exists())
        config = json.loads((self.destination / "sbom.config.json").read_text())
        self.assertEqual(config["rootComponent"]["version"], "1.7.3")
        self.assertEqual(config["releaseTag"], "v1.7.3")
        self.assertTrue(all(component["releaseRepository"] == selected["repository"]
                            for component in config["imageComponents"]))
        for destination_name, (component, _) in COMPONENTS.items():
            expected = (self.source / "release/sboms" / f"{component}.cdx.json").read_bytes()
            self.assertEqual((self.destination / "sboms" / f"{destination_name}.cdx.json").read_bytes(), expected)

        defaults = task_defaults()
        environment = Environment(undefined=StrictUndefined)
        context = {"digital_energy_twin_application_release": selected}
        for key, component in (("admin", "adminfrontend"), ("public", "frontend"),
                               ("backend", "backend"), ("matomo", "matomo")):
            rendered = environment.from_string(defaults[f"digital_energy_twin_{key}_image"]).render(context)
            image = self.manifest[component]
            self.assertEqual(rendered, f"{image['image']}@{image['digest']}")
        self.assertEqual(environment.from_string(defaults["digital_energy_twin_application_version"])
                         .render(context), "1.7.3")
        for name in ("public", "admin"):
            template = environment.from_string((ROOT / f"templates/digital_energy_twin_{name}_deploy.yaml.j2").read_text())
            deployment = yaml.safe_load(template.render({
                f"digital_energy_twin_{name}_image": "example@sha256:" + "1" * 64,
                f"digital_energy_twin_{name}_port": 8080,
                "digital_energy_twin_application_version": "1.7.3",
            }))
            self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0]["env"],
                             [{"name": "APP_VERSION", "value": "1.7.3"}])

    def test_release_without_matomo_is_adopted_and_skips_matomo(self):
        self.remove_matomo_from_release()
        stale = self.destination / "sboms/matomo.cdx.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}")
        result = self.adopt()
        self.assertNotIn("matomo", result["images"])
        self.assertFalse(stale.exists())
        for destination_name in ("admin", "public", "backend"):
            self.assertTrue((self.destination / "sboms" / f"{destination_name}.cdx.json").exists())
        selected = json.loads((self.destination / "vars/application_release.json").read_text())[
            "digital_energy_twin_application_release"]
        self.assertNotIn("matomo", selected["manifest"])
        rendered = Environment(undefined=StrictUndefined).from_string(
            task_defaults()["digital_energy_twin_matomo_image"]).render(
            digital_energy_twin_application_release=selected)
        self.assertEqual(rendered, "")

    def test_rejects_matomo_selection_when_release_has_no_matomo(self):
        self.remove_matomo_from_release()
        selection = json.loads(self.selection_file.read_text())
        selection["images"]["matomo"] = "ghcr.io/example/matomo@sha256:" + "f" * 64
        self.selection_file.write_text(json.dumps(selection))
        with self.assertRaisesRegex(ValueError, "Validated selection differs for matomo"):
            self.adopt()
        self.assertFalse((self.destination / "vars/application_release.json").exists())

    def test_rejects_mismatched_selection_before_changing_deployment_inputs(self):
        selection = json.loads(self.selection_file.read_text())
        selection["images"]["backend"] = "ghcr.io/example/backend@sha256:" + "f" * 64
        self.selection_file.write_text(json.dumps(selection))
        with self.assertRaisesRegex(ValueError, "Validated selection differs for backend"):
            self.adopt()
        self.assertTrue((self.destination / "vars/software_references.yml").exists())
        self.assertFalse((self.destination / "vars/application_release.json").exists())

    def test_rejects_addon_tag_that_differs_from_monorepo_release(self):
        with self.assertRaisesRegex(ValueError, "Addon tag must match the monorepo release tag"):
            prepare_release(self.source, self.release_file, self.selection_file,
                            "v1.8.0", self.destination)
        self.assertTrue((self.destination / "vars/software_references.yml").exists())
        self.assertFalse((self.destination / "vars/application_release.json").exists())

    def test_rejects_changed_sbom_bytes_before_changing_deployment_inputs(self):
        file = self.source / "release/sboms/frontend.cdx.json"
        file.write_bytes(file.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "Release SBOM hash differs for frontend"):
            self.adopt()
        self.assertTrue((self.destination / "vars/software_references.yml").exists())
        self.assertFalse((self.destination / "vars/application_release.json").exists())

    def test_existing_selection_remains_active_before_release_adoption(self):
        defaults = task_defaults()
        software = yaml.safe_load(SOFTWARE_REFERENCES)["software"]
        environment = Environment(undefined=StrictUndefined)
        for key in ("admin", "public", "backend"):
            rendered = environment.from_string(defaults[f"digital_energy_twin_{key}_image"]).render(
                software=software)
            selected = software["digital-energy-twin"][key]
            self.assertEqual(rendered, f"{selected['registry']}/{selected['repository']}:{selected['tag']}")
        self.assertEqual(environment.from_string(defaults["digital_energy_twin_application_version"])
                         .render(software=software), "")
        self.assertEqual(environment.from_string(defaults["digital_energy_twin_matomo_image"])
                         .render(software=software), "")


if __name__ == "__main__":
    unittest.main()
