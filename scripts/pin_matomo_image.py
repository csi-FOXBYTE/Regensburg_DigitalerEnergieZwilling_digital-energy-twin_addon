#!/usr/bin/env python3
"""Pin a freshly built Matomo image and its SBOM in the addon.

Called by the workflow "Build Matomo image" after the push. Takes the SBOM the
image carries (/usr/share/regensburg/SBOM.cdx.json), stamps it like the app
release does for its images and writes sboms/matomo.cdx.json and
vars/matomo_image.json. Changes nothing if any check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from pathlib import Path


ADDON_REPOSITORY = "csi-FOXBYTE/Regensburg_DigitalerEnergieZwilling_digital-energy-twin_addon"
PACKAGE_NAME = "regensburg-web-matomo"
SBOM_PATH = "sboms/matomo.cdx.json"
PIN_PATH = "vars/matomo_image.json"
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
COMMIT = re.compile(r"^[a-f0-9]{40}$")
BUILD = re.compile(r"^\d+-\d+$")
PINNED_IMAGE = re.compile(r"^[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def pin(destination: Path, *, image: str, digest: str, build: str, source: str, platform: str,
        base_image: str, runtime_base_image: str, embedded_sbom: Path, timestamp: str) -> dict:
    require(re.fullmatch(r"[a-z0-9.-]+(/[a-z0-9._-]+)+", image) is not None, "Invalid image repository")
    require(DIGEST.fullmatch(digest) is not None, "Invalid image digest")
    require(BUILD.fullmatch(build) is not None, "Invalid build ID")
    require(COMMIT.fullmatch(source) is not None, "Invalid source commit")
    require(PINNED_IMAGE.fullmatch(base_image) is not None
            and PINNED_IMAGE.fullmatch(runtime_base_image) is not None, "Base images must be pinned by digest")
    require(TIMESTAMP.fullmatch(timestamp) is not None, "Timestamp must be UTC ISO 8601")

    bom = json.loads(embedded_sbom.read_text(encoding="utf-8"))
    root = bom.get("metadata", {}).get("component", {})
    properties = {item["name"]: item["value"] for item in root.get("properties", [])}
    require(bom.get("bomFormat") == "CycloneDX" and root.get("name") == PACKAGE_NAME,
            "The image SBOM is not the Matomo image SBOM")
    require(root.get("version") == f"build-{build}" and properties.get("regensburg:build") == build,
            "The image SBOM belongs to a different build")
    require(properties.get("vcs:commit") == source, "The image SBOM belongs to a different source commit")
    base = next((c for c in bom.get("components", []) if c.get("type") == "container"), {})
    require({"alg": "SHA-256", "content": runtime_base_image.rsplit("@sha256:", 1)[1]} in base.get("hashes", []),
            "The image SBOM names a different Matomo base image")

    # Same stamping as the app release's child SBOMs; a stable UUIDv5 keeps a
    # rerun for the same build byte-identical.
    identity = {"filename": "matomo.cdx.json", "digest": digest, "build": build, "sourceCommit": source}
    bom["serialNumber"] = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_DNS, json.dumps(identity, sort_keys=True))}"
    bom["metadata"]["timestamp"] = timestamp
    bom["metadata"]["properties"] = [
        item for item in bom["metadata"].get("properties", [])
        if not item["name"].startswith("regensburg:release-") and item["name"] != "regensburg:image-digest"
    ] + [
        {"name": "regensburg:release-source", "value": source},
        {"name": "regensburg:release-inventory",
         "value": "Fresh inventory snapshot; licenses and copyrights have not been audited"},
        {"name": "regensburg:image-digest", "value": digest},
    ]
    content = (json.dumps(bom, indent=2) + "\n").encode("utf-8")
    selected = {"digital_energy_twin_matomo_release": {
        "image": image, "digest": digest, "build": build, "sourceCommit": source,
        "releaseSource": source, "releaseRepository": ADDON_REPOSITORY, "platform": platform,
        "baseImage": base_image, "runtimeBaseImage": runtime_base_image,
        "sbom": {"path": SBOM_PATH, "sha256": f"sha256:{hashlib.sha256(content).hexdigest()}"},
    }}
    for path in (SBOM_PATH, PIN_PATH):
        (destination / path).parent.mkdir(parents=True, exist_ok=True)
    (destination / SBOM_PATH).write_bytes(content)
    (destination / PIN_PATH).write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    return selected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("image", "digest", "build", "source", "platform", "base-image", "runtime-base-image",
                 "embedded-sbom", "timestamp"):
        parser.add_argument(f"--{name}", required=True)
    arguments = parser.parse_args()
    result = pin(Path(__file__).resolve().parents[1], image=arguments.image, digest=arguments.digest,
                 build=arguments.build, source=arguments.source, platform=arguments.platform,
                 base_image=arguments.base_image, runtime_base_image=arguments.runtime_base_image,
                 embedded_sbom=Path(arguments.embedded_sbom), timestamp=arguments.timestamp)
    release = result["digital_energy_twin_matomo_release"]
    print(f"Pinned {release['image']}@{release['digest']} (build {release['build']})")
