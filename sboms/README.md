# Modular application SBOMs

The release-preparation workflow verifies the CycloneDX SBOM attestation for
each digest-pinned application image and writes the verified predicates here:

- `admin.cdx.json`
- `public.cdx.json`
- `backend.cdx.json`

The addon SBOM links each container component to its corresponding child BOM
using a CycloneDX BOM-Link, a release-pinned download URL, and cryptographic
hashes. Do not populate these files from an unverified branch or mutable image
tag.
