#!/usr/bin/env python3
"""Generate repository CycloneDX and CSV software bills of materials."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cyclonedx.schema import OutputFormat, SchemaVersion
from cyclonedx.validation import make_schemabased_validator


REPOSITORY_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPOSITORY_DIR / "sbom.config.json"
SOFTWARE_REFERENCES_PATH = REPOSITORY_DIR / "vars" / "software_references.yml"
JSON_OUTPUT_PATH = REPOSITORY_DIR / "SBOM.cdx.json"
CSV_OUTPUT_PATH = REPOSITORY_DIR / "SBOM.csv"
JSON_TEMPORARY_PATH = REPOSITORY_DIR / ".SBOM.cdx.tmp.json"
CSV_TEMPORARY_PATH = REPOSITORY_DIR / ".SBOM.tmp.csv"
REQUIRE_CHILD_BOMS = os.environ.get("SBOM_REQUIRE_CHILD_BOMS", "").lower() in {
    "1",
    "true",
    "yes",
}


def require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value


def run(command: list[str]) -> str:
    return subprocess.run(
        command,
        cwd=REPOSITORY_DIR,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout


def set_property(component: dict[str, Any], name: str, value: str) -> None:
    component.setdefault("properties", []).append({"name": name, "value": value})


def get_property(component: dict[str, Any], name: str) -> str | None:
    return next(
        (
            entry.get("value")
            for entry in component.get("properties", [])
            if entry.get("name") == name
        ),
        None,
    )


def read_image_references() -> dict[str, dict[str, str]]:
    """Read the small, fixed-shape image map without adding a YAML dependency."""
    images: dict[str, dict[str, str]] = {}
    current: str | None = None
    section_pattern = re.compile(r"^    ([a-z][a-z0-9_-]*):\s*$")
    value_pattern = re.compile(r'^      (registry|repository|tag):\s*"([^"\\]+)"\s*$')

    for line in SOFTWARE_REFERENCES_PATH.read_text(encoding="utf-8").splitlines():
        section_match = section_pattern.match(line)
        if section_match:
            current = section_match.group(1)
            images[current] = {}
            continue
        value_match = value_pattern.match(line)
        if current and value_match:
            images[current][value_match.group(1)] = value_match.group(2)

    for key, values in images.items():
        for field in ("registry", "repository", "tag"):
            require_string(values.get(field), f"software reference {key}.{field}")
    return images


def read_child_bom(
    entry: dict[str, Any], location: str
) -> tuple[dict[str, Any], Path, str, list[dict[str, str]]] | None:
    relative_path = require_string(
        entry.get("sbomPath"), f"{location}.sbomPath"
    )
    path = REPOSITORY_DIR / relative_path
    if not path.is_file():
        if REQUIRE_CHILD_BOMS:
            raise ValueError(f"Required child SBOM does not exist: {relative_path}")
        return None

    content = path.read_bytes()
    try:
        bom = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"Child SBOM is not valid JSON: {relative_path}") from error
    if bom.get("bomFormat") != "CycloneDX":
        raise ValueError(f"Child SBOM is not CycloneDX: {relative_path}")
    serial_number = require_string(
        bom.get("serialNumber"), f"{relative_path}.serialNumber"
    )
    if not serial_number.startswith("urn:uuid:"):
        raise ValueError(f"{relative_path}.serialNumber must be a UUID URN")
    document_version = bom.get("version")
    if not isinstance(document_version, int) or document_version < 1:
        raise ValueError(f"{relative_path}.version must be a positive integer")
    child_root = bom.get("metadata", {}).get("component")
    if not isinstance(child_root, dict):
        raise ValueError(f"{relative_path} must have a metadata.component object")
    require_string(child_root.get("bom-ref"), f"{relative_path}.metadata.component.bom-ref")

    validation_error = make_schemabased_validator(
        OutputFormat.JSON, SchemaVersion.V1_6
    ).validate_str(content.decode("utf-8"))
    if validation_error:
        raise ValueError(
            f"Child SBOM is invalid against CycloneDX 1.6 ({relative_path}): "
            f"{validation_error}"
        )

    bom_link = f"urn:cdx:{serial_number.removeprefix('urn:uuid:')}/{document_version}"
    hashes = [
        {"alg": "SHA-256", "content": hashlib.sha256(content).hexdigest()},
        {"alg": "SHA-512", "content": hashlib.sha512(content).hexdigest()},
    ]
    return bom, path, bom_link, hashes


def annotate_component(
    component: dict[str, Any],
    repository: str,
    ecosystem: str,
    relationship: str,
    metadata_source: str,
    notes: str,
) -> None:
    set_property(component, "sbom:repository", repository)
    set_property(component, "sbom:ecosystem", ecosystem)
    set_property(component, "sbom:relationship", relationship)
    set_property(component, "sbom:metadata-source", f"{repository}/{metadata_source}")
    set_property(component, "sbom:notes", notes)


def create_bom(config: dict[str, Any]) -> dict[str, Any]:
    root = config.get("rootComponent")
    if not isinstance(root, dict):
        raise ValueError("sbom.config.json must contain a rootComponent object")
    root = dict(root)
    for field in ("type", "bom-ref", "name", "version"):
        require_string(root.get(field), f"rootComponent.{field}")
    repository = root["name"]
    release_tag = require_string(config.get("releaseTag"), "releaseTag")
    repository_slug = require_string(
        config.get("repositorySlug"), "repositorySlug"
    )

    annotate_component(
        root,
        repository,
        "first-party",
        "root component",
        "sbom.config.json",
        "First-party repository component.",
    )
    try:
        set_property(root, "vcs:commit", run(["git", "rev-parse", "HEAD"]).strip())
        status = run(["git", "status", "--porcelain"]).strip()
        set_property(
            root,
            "sbom:source-state",
            "clean" if not status else "modified working tree",
        )
    except (OSError, subprocess.CalledProcessError):
        set_property(root, "sbom:source-state", "Git state unavailable")

    components: list[dict[str, Any]] = []
    component_dependencies: dict[str, list[str]] = {}
    child_bom_count = 0
    image_references = read_image_references()
    image_config = config.get("imageComponents")
    if not isinstance(image_config, list):
        raise ValueError("sbom.config.json must contain an imageComponents array")
    configured_keys: set[str] = set()
    for index, entry in enumerate(image_config):
        location = f"imageComponents[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{location} must be an object")
        source_key = require_string(entry.get("sourceKey"), f"{location}.sourceKey")
        if source_key not in image_references:
            raise ValueError(f"{location}.sourceKey does not exist in software references")
        configured_keys.add(source_key)
        reference = image_references[source_key]
        name = f"{reference['registry']}/{reference['repository']}"
        tag = reference["tag"]
        component = {
            "type": "container",
            "bom-ref": f"oci:{name}:{tag}",
            "name": name,
            "version": tag,
            "scope": "required",
            "licenses": [{"license": {"id": require_string(entry.get("license"), f"{location}.license")}}],
        }
        annotate_component(
            component,
            repository,
            "OCI container",
            require_string(entry.get("relationship"), f"{location}.relationship"),
            "vars/software_references.yml",
            require_string(entry.get("notes"), f"{location}.notes"),
        )
        child_bom = read_child_bom(entry, location)
        if child_bom:
            child, child_path, bom_link, hashes = child_bom
            child_root_ref = child["metadata"]["component"]["bom-ref"]
            relative_path = child_path.relative_to(REPOSITORY_DIR).as_posix()
            release_url = (
                f"https://raw.githubusercontent.com/{repository_slug}/"
                f"{quote(release_tag, safe='')}/{relative_path}"
            )
            component["externalReferences"] = [
                {
                    "type": "bom",
                    "url": bom_link,
                    "comment": (
                        f"CycloneDX BOM-Link for the verified child SBOM bundled at "
                        f"{relative_path}."
                    ),
                    "hashes": hashes,
                },
                {
                    "type": "bom",
                    "url": release_url,
                    "comment": "Release-pinned download location for the bundled child SBOM.",
                    "hashes": hashes,
                },
            ]
            set_property(component, "sbom:child-bom-path", relative_path)
            set_property(component, "sbom:child-bom-link", bom_link)
            set_property(
                component,
                "sbom:attestation-repository",
                require_string(
                    entry.get("attestationRepository"),
                    f"{location}.attestationRepository",
                ),
            )
            component_dependencies[component["bom-ref"]] = [
                f"{bom_link}#{quote(child_root_ref, safe='')}"
            ]
            child_bom_count += 1
        else:
            set_property(component, "sbom:child-bom-status", "not bundled")
            component_dependencies[component["bom-ref"]] = []
        components.append(component)
    unconfigured_keys = sorted(set(image_references) - configured_keys)
    if unconfigured_keys:
        raise ValueError(
            "Software references are missing SBOM configuration: "
            + ", ".join(unconfigured_keys)
        )

    additional_config = config.get("additionalComponents")
    if not isinstance(additional_config, list):
        raise ValueError("sbom.config.json must contain an additionalComponents array")
    for index, entry in enumerate(additional_config):
        location = f"additionalComponents[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{location} must be an object")
        component = dict(entry)
        ecosystem = require_string(component.pop("ecosystem", None), f"{location}.ecosystem")
        relationship = require_string(component.pop("relationship", None), f"{location}.relationship")
        metadata_source = require_string(component.pop("metadataSource", None), f"{location}.metadataSource")
        notes = require_string(component.pop("notes", None), f"{location}.notes")
        license_value = require_string(component.pop("license", None), f"{location}.license")
        for field in ("type", "bom-ref", "name", "version"):
            require_string(component.get(field), f"{location}.{field}")
        component["licenses"] = [
            {"license": {"name": "NOASSERTION"}}
            if license_value == "NOASSERTION"
            else {"license": {"id": license_value}}
        ]
        annotate_component(
            component,
            repository,
            ecosystem,
            relationship,
            metadata_source,
            notes,
        )
        components.append(component)

    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": (
            "urn:uuid:"
            + str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://github.com/{repository_slug}/releases/tag/{release_tag}",
                )
            )
        ),
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "repository SBOM generation script",
                        "version": "1",
                    }
                ]
            },
            "component": root,
            "properties": [
                {
                    "name": "sbom:scope",
                    "value": (
                        "The addon, all application containers it deploys, its control-plane "
                        "dependencies, and platform services directly required or integrated"
                    ),
                },
                {
                    "name": "sbom:container-detail",
                    "value": (
                        f"Container components are version-tagged inventory references; "
                        f"{child_bom_count} of {len(image_config)} verified child SBOMs are "
                        "bundled and connected through hashed CycloneDX BOM-Link references"
                    ),
                },
                {
                    "name": "sbom:unresolved-policy",
                    "value": (
                        "Deployment-supplied versions and NOASSERTION licenses are retained for "
                        "manual review; no version or license is guessed"
                    ),
                },
            ],
        },
        "components": components,
        "dependencies": [
            {
                "ref": root["bom-ref"],
                "dependsOn": sorted(component["bom-ref"] for component in components),
            },
            *[
                {
                    "ref": component["bom-ref"],
                    "dependsOn": component_dependencies.get(component["bom-ref"], []),
                }
                for component in components
            ],
        ],
    }


def validate_bom(bom: dict[str, Any]) -> str:
    components = [bom["metadata"]["component"], *bom["components"]]
    refs = [component.get("bom-ref") for component in components]
    if len(refs) != len(set(refs)):
        raise ValueError("SBOM contains duplicate component bom-ref values")
    output = json.dumps(bom, indent=2) + "\n"
    validation_error = make_schemabased_validator(
        OutputFormat.JSON, SchemaVersion.V1_6
    ).validate_str(output)
    if validation_error:
        raise ValueError(f"SBOM is invalid against CycloneDX 1.6: {validation_error}")
    return output


def license_value(component: dict[str, Any]) -> str:
    license_data = component.get("licenses", [{}])[0].get("license", {})
    return license_data.get("id") or license_data.get("name") or "NOASSERTION"


def create_csv(bom: dict[str, Any]) -> None:
    repository = bom["metadata"]["component"]["name"]
    components = [bom["metadata"]["component"], *bom["components"]]

    def rank(component: dict[str, Any]) -> tuple[int, str]:
        if component is bom["metadata"]["component"]:
            component_rank = 0
        elif component.get("type") == "container":
            component_rank = 1
        else:
            component_rank = 2
        return component_rank, component.get("name", "").lower()

    with CSV_TEMPORARY_PATH.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "Repository",
                "Component Type",
                "Ecosystem",
                "Name",
                "Version",
                "Dependency Scope",
                "Relationship",
                "License",
                "PURL or Reference",
                "Metadata Source",
                "Notes",
            ]
        )
        for component in sorted(components, key=rank):
            writer.writerow(
                [
                    repository,
                    component.get("type", ""),
                    get_property(component, "sbom:ecosystem") or "",
                    component.get("name", ""),
                    component.get("version", ""),
                    component.get("scope", "required"),
                    get_property(component, "sbom:relationship") or "",
                    license_value(component),
                    component.get("purl") or component.get("bom-ref", ""),
                    get_property(component, "sbom:metadata-source") or "",
                    get_property(component, "sbom:notes") or "",
                ]
            )


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    bom = create_bom(config)
    try:
        JSON_TEMPORARY_PATH.write_text(validate_bom(bom), encoding="utf-8")
        create_csv(bom)
        JSON_TEMPORARY_PATH.replace(JSON_OUTPUT_PATH)
        CSV_TEMPORARY_PATH.replace(CSV_OUTPUT_PATH)
    finally:
        JSON_TEMPORARY_PATH.unlink(missing_ok=True)
        CSV_TEMPORARY_PATH.unlink(missing_ok=True)
    print(
        f"Generated {JSON_OUTPUT_PATH.name} and {CSV_OUTPUT_PATH.name} "
        f"with {len(bom['components'])} components."
    )


if __name__ == "__main__":
    main()
