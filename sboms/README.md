# Modular application SBOMs

The release-preparation workflow reads all three application image digests from
one completed monorepo release manifest. It checks each child CycloneDX SBOM
against that manifest's hash, release source, image digest and build identity,
then writes the verified documents here:

- `admin.cdx.json`
- `public.cdx.json`
- `backend.cdx.json`

The addon SBOM links each container component to its corresponding child BOM
using a CycloneDX BOM-Link, a release-pinned download URL, and cryptographic
hashes. Do not populate these files from an unverified branch or mutable image
tag. The currently checked-in documents describe the earlier tag-based selection
until the v1.7.3 monorepo release is adopted.
