#!/usr/bin/env python3
"""Record one validated monorepo release as the addon's application selection."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


APPLICATION_REPOSITORY = "csi-FOXBYTE/Regensburg_DigitalerEnergieZwilling_MonoRepo"
COMPONENTS = {
    "admin": ("adminfrontend", "base-frontend"),
    "public": ("frontend", "frontend"),
    "backend": ("backend", "rg-dez-backend"),
}
# The addon builds and pins the Matomo image itself (images/matomo,
# vars/matomo_image.json). App releases that still contain one (v1.8.0) are
# adopted without it.


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"Expected JSON object: {path}")
    return value


def property_value(bom: dict, name: str) -> str | None:
    return next((item.get("value") for item in bom.get("metadata", {}).get("properties", [])
                 if item.get("name") == name), None)


def prepare_release(source: Path, release_file: Path, selection_file: Path,
                    addon_tag: str, destination: Path) -> dict:
    require(re.fullmatch(r"v?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", addon_tag) is not None,
            "Addon tag must be a semantic version")
    manifest = read_json(source / "release/manifest.json")
    release = read_json(release_file)
    selection = read_json(selection_file)
    tag = f"v{manifest['appVersion']}"
    require(addon_tag == tag, "Addon tag must match the monorepo release tag")
    require(release.get("tag_name") == tag and not release.get("draft"),
            "The selected GitHub release must be completed and match the tagged manifest")
    require(selection.get("appVersion") == manifest["appVersion"]
            and selection.get("sourceCommit") == manifest["sourceCommit"],
            "The validated release selection differs from the tagged manifest")
    require(manifest.get("sboms", {}).get("sourceCommit") == manifest["sourceCommit"],
            "Release SBOM source differs from the manifest")
    files = manifest["sboms"]["files"]
    selected_images = selection.get("images", {})
    verified_sboms: dict[str, bytes] = {}
    for destination_name, (component, package_name) in COMPONENTS.items():
        image = manifest[component]
        reference = f"{image['image']}@{image['digest']}"
        require(selected_images.get(component) == reference,
                f"Validated selection differs for {component}")
        require(manifest["sboms"]["images"].get(component) == {
            "digest": image["digest"], "sourceCommit": image["sourceCommit"],
            "build": image["build"]}, f"Release SBOM receipt differs for {component}")
        filename = f"{component}.cdx.json"
        content = (source / "release/sboms" / filename).read_bytes()
        require(f"sha256:{hashlib.sha256(content).hexdigest()}" == files.get(filename),
                f"Release SBOM hash differs for {component}")
        bom = json.loads(content)
        root = bom.get("metadata", {}).get("component", {})
        require(bom.get("bomFormat") == "CycloneDX"
                and root.get("name") == package_name
                and root.get("version") == f"build-{image['build']}"
                and property_value(bom, "regensburg:image-digest") == image["digest"]
                and property_value(bom, "regensburg:release-source") == manifest["sourceCommit"],
                f"Release SBOM identity differs for {component}")
        verified_sboms[destination_name] = content

    config_file = destination / "sbom.config.json"
    config = read_json(config_file)
    for destination_name, content in verified_sboms.items():
        target = destination / "sboms" / f"{destination_name}.cdx.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    selected = {"digital_energy_twin_application_release": {
        "repository": APPLICATION_REPOSITORY, "tag": tag, "manifest": manifest}}
    selected_file = destination / "vars/application_release.json"
    selected_file.parent.mkdir(parents=True, exist_ok=True)
    selected_file.write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")

    config["releaseTag"] = addon_tag
    config["rootComponent"]["version"] = addon_tag.removeprefix("v")
    purl = ("pkg:generic/Regensburg_DigitalerEnergieZwilling_digital-energy-twin_addon@"
            + addon_tag.removeprefix("v"))
    config["rootComponent"]["bom-ref"] = purl
    config["rootComponent"]["purl"] = purl
    for component in config["imageComponents"]:
        if component["sourceKey"] not in COMPONENTS:
            continue
        component.pop("attestationRepository", None)
        component["releaseRepository"] = APPLICATION_REPOSITORY
    config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (destination / "vars/software_references.yml").unlink(missing_ok=True)
    return {"applicationTag": tag, "appVersion": manifest["appVersion"],
            "images": {component: selected_images[component] for component, _ in COMPONENTS.values()}}


if __name__ == "__main__":
    if len(sys.argv) != 6:
        raise SystemExit("Usage: adopt_application_release.py SOURCE_DIR RELEASE_JSON "
                         "SELECTION_JSON ADDON_TAG ADDON_DIR")
    print(json.dumps(prepare_release(*map(Path, sys.argv[1:4]), sys.argv[4],
                                     Path(sys.argv[5])), indent=2))
