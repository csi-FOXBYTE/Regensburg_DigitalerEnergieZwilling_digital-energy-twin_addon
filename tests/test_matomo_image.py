"""Offline checks for the Matomo image this addon builds and pins itself."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.pin_matomo_image import ADDON_REPOSITORY, pin


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = ROOT / "images/matomo"
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/matomo-image.yml").read_text())
BUILD = {"build": "123-1", "source": "c" * 40, "digest": "sha256:" + "e" * 64,
         "base_image": "node:24.22.1-bookworm-slim@sha256:" + "a" * 64,
         "runtime_base_image": "matomo:5.14.0-apache@sha256:" + "b" * 64}


def embedded_sbom(directory: Path, build: str, source: str, runtime_base_image: str) -> Path:
    """The SBOM the Dockerfile's Node stage writes into the image."""
    output = directory / "embedded.json"
    subprocess.run(["node", str(IMAGE_DIR / "sbom.mjs"), str(output)], check=True,
                   env={**os.environ, "BUILD_ID": build, "SOURCE_COMMIT": source,
                        "RUNTIME_BASE_IMAGE": runtime_base_image})
    return output


class MatomoImageTests(unittest.TestCase):
    def test_dockerfile_downloads_exactly_the_plugin_the_sbom_names(self):
        sbom = (IMAGE_DIR / "sbom.mjs").read_text()
        url = re.search(r"url: '([^']+)'", sbom).group(1)
        sha256 = re.search(r"sha256: '([a-f0-9]{64})'", sbom).group(1)
        dockerfile = (IMAGE_DIR / "Dockerfile").read_text()
        self.assertIn(f"ADD --checksum=sha256:{sha256} \\\n  {url} ", dockerfile)

    def test_workflow_builds_from_the_dockerfile_defaults(self):
        dockerfile = (IMAGE_DIR / "Dockerfile").read_text()
        self.assertIn(f"ARG NODE_BASE_IMAGE={WORKFLOW['env']['NODE_BASE']}\n", dockerfile)
        self.assertIn(f"ARG RUNTIME_BASE_IMAGE={WORKFLOW['env']['MATOMO_BASE']}\n", dockerfile)
        pin = json.loads((ROOT / "vars/matomo_image.json").read_text())["digital_energy_twin_matomo_release"]
        self.assertEqual(pin["image"], WORKFLOW["env"]["IMAGE"])
        self.assertEqual(pin["platform"], WORKFLOW["env"]["PLATFORM"])

    def test_workflow_runs_only_on_main_and_never_beside_the_release(self):
        release = yaml.safe_load((ROOT / ".github/workflows/pin-digests.yml").read_text())
        self.assertEqual(WORKFLOW["concurrency"], release["concurrency"])
        self.assertEqual(WORKFLOW["jobs"]["build-matomo"]["if"], "github.ref == 'refs/heads/main'")
        steps = {step.get("name"): step for step in WORKFLOW["jobs"]["build-matomo"]["steps"]}
        self.assertIn("scripts/pin_matomo_image.py", steps["Pin the image and its SBOM"]["run"])
        self.assertIn("SBOM_REQUIRE_CHILD_BOMS=1", steps["Regenerate the addon SBOM"]["run"])
        self.assertIn("git add -- vars/matomo_image.json sboms/matomo.cdx.json SBOM.cdx.json SBOM.csv",
                      steps["Commit the pin on main"]["run"])

    def test_current_pin_matches_its_sbom(self):
        # The same checks scripts/generate_sbom.py makes for every image.
        pin = json.loads((ROOT / "vars/matomo_image.json").read_text())["digital_energy_twin_matomo_release"]
        content = (ROOT / pin["sbom"]["path"]).read_bytes()
        self.assertEqual(pin["sbom"]["sha256"], "sha256:" + hashlib.sha256(content).hexdigest())
        metadata = json.loads(content)["metadata"]
        properties = {item["name"]: item["value"] for item in metadata["properties"]}
        root = {item["name"]: item["value"] for item in metadata["component"]["properties"]}
        self.assertEqual(properties["regensburg:image-digest"], pin["digest"])
        self.assertEqual(properties["regensburg:release-source"], pin["releaseSource"])
        self.assertEqual(metadata["component"]["version"], f"build-{pin['build']}")
        self.assertEqual(root["vcs:commit"], pin["sourceCommit"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_pins_a_new_build_and_its_sbom(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sbom = embedded_sbom(directory, BUILD["build"], BUILD["source"], BUILD["runtime_base_image"])
            arguments = dict(image="ghcr.io/csi-foxbyte/regensburg-web-matomo", digest=BUILD["digest"],
                             build=BUILD["build"], source=BUILD["source"], platform="linux/amd64",
                             base_image=BUILD["base_image"], runtime_base_image=BUILD["runtime_base_image"],
                             embedded_sbom=sbom, timestamp="2026-10-19T09:00:00Z")
            release = pin(directory, **arguments)["digital_energy_twin_matomo_release"]
            child = (directory / "sboms/matomo.cdx.json").read_bytes()
            self.assertEqual(release["sbom"]["sha256"], "sha256:" + hashlib.sha256(child).hexdigest())
            self.assertEqual(release["releaseRepository"], ADDON_REPOSITORY)
            bom = json.loads(child)
            self.assertTrue(bom["serialNumber"].startswith("urn:uuid:"))
            self.assertEqual({item["name"]: item["value"] for item in bom["metadata"]["properties"]}, {
                "regensburg:release-source": BUILD["source"],
                "regensburg:release-inventory": "Fresh inventory snapshot; licenses and copyrights have not been audited",
                "regensburg:image-digest": BUILD["digest"]})
            self.assertEqual(json.loads((directory / "vars/matomo_image.json").read_text()),
                             {"digital_energy_twin_matomo_release": release})
            # A rerun for the same build changes nothing.
            pin(directory, **arguments)
            self.assertEqual((directory / "sboms/matomo.cdx.json").read_bytes(), child)
            try:
                from cyclonedx.schema import OutputFormat, SchemaVersion
                from cyclonedx.validation import make_schemabased_validator
            except ImportError:
                return
            self.assertIsNone(make_schemabased_validator(OutputFormat.JSON, SchemaVersion.V1_6)
                              .validate_str(child.decode()))

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_refuses_an_sbom_from_another_build_without_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sbom = embedded_sbom(directory, BUILD["build"], BUILD["source"], BUILD["runtime_base_image"])
            base = dict(image="ghcr.io/csi-foxbyte/regensburg-web-matomo", digest=BUILD["digest"],
                        build=BUILD["build"], source=BUILD["source"], platform="linux/amd64",
                        base_image=BUILD["base_image"], runtime_base_image=BUILD["runtime_base_image"],
                        embedded_sbom=sbom, timestamp="2026-10-19T09:00:00Z")
            for change, message in (({"build": "124-1"}, "different build"),
                                    ({"source": "d" * 40}, "different source commit"),
                                    ({"runtime_base_image": "matomo:5.14.0-apache@sha256:" + "f" * 64},
                                     "different Matomo base image"),
                                    ({"base_image": "node:24"}, "pinned by digest")):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                    pin(directory, **{**base, **change})
            self.assertFalse((directory / "vars").exists())
            self.assertFalse((directory / "sboms").exists())


if __name__ == "__main__":
    unittest.main()
