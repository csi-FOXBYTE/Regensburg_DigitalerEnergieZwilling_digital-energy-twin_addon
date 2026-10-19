// Writes this image's CycloneDX SBOM to /usr/share/regensburg/SBOM.cdx.json,
// where the addon's image workflow reads it, as for the app images. Runs in
// the Dockerfile's Node stage. Usage: node sbom.mjs output.json
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// Must match the ADD line in the Dockerfile (checked by tests/test_matomo_image.py).
export const loginOidc = {
  version: '5.0.0',
  url: 'https://github.com/dominik-th/matomo-plugin-LoginOIDC/archive/refs/tags/5.0.0.tar.gz',
  sha256: '265433ec2cc526b627e3bb124c76c3521309d2a8becce06db95d89ce65a60bb2',
};
export const packageName = 'regensburg-web-matomo';

export function matomoBom({ build, source, baseImage, noPasswordResetVersion }) {
  assert(build === 'local' || /^\d+-\d+$/.test(build), 'Invalid build ID');
  assert(source === 'local' || /^[a-f0-9]{40}$/.test(source), 'Invalid source commit');
  const [, baseName, baseTag, baseDigest] = baseImage.match(/^([^:@]+):([^@]+)(?:@sha256:([a-f0-9]{64}))?$/)
    ?? assert.fail(`Unexpected base image ${baseImage}`);
  const root = `regensburg:matomo:build-${build}`;
  const base = { type: 'container', name: baseName, version: baseTag, 'bom-ref': `docker:${baseName}:${baseTag}`,
    licenses: [{ expression: 'GPL-3.0-or-later' }],
    ...(baseDigest ? { hashes: [{ alg: 'SHA-256', content: baseDigest }] } : {}) };
  const plugin = { type: 'library', name: 'LoginOIDC', version: loginOidc.version,
    'bom-ref': `pkg:github/dominik-th/matomo-plugin-LoginOIDC@${loginOidc.version}`,
    purl: `pkg:github/dominik-th/matomo-plugin-LoginOIDC@${loginOidc.version}`,
    licenses: [{ expression: 'GPL-3.0-or-later' }],
    hashes: [{ alg: 'SHA-256', content: loginOidc.sha256 }],
    externalReferences: [{ type: 'distribution', url: loginOidc.url }] };
  const guard = { type: 'library', name: 'NoPasswordReset', version: noPasswordResetVersion,
    'bom-ref': `regensburg:matomo-plugin:NoPasswordReset@${noPasswordResetVersion}`,
    licenses: [{ expression: 'GPL-3.0-or-later' }] };
  return {
    bomFormat: 'CycloneDX', specVersion: '1.6', version: 1,
    metadata: { component: { type: 'application', name: packageName, version: `build-${build}`, 'bom-ref': root,
      licenses: [{ expression: 'GPL-3.0-or-later' }],
      properties: [{ name: 'vcs:commit', value: source }, { name: 'regensburg:build', value: build },
        { name: 'sbom:source-state', value: source === 'local' ? 'local validation only' : 'pinned Git build context' }] } },
    components: [base, plugin, guard],
    dependencies: [{ ref: root, dependsOn: [base['bom-ref'], plugin['bom-ref'], guard['bom-ref']] }],
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const output = process.argv[2];
  assert(output, 'Usage: node sbom.mjs output.json');
  const guardPlugin = JSON.parse(readFileSync(new URL('./plugins/NoPasswordReset/plugin.json', import.meta.url)));
  const bom = matomoBom({ build: process.env.BUILD_ID ?? 'local', source: process.env.SOURCE_COMMIT ?? 'local',
    baseImage: process.env.RUNTIME_BASE_IMAGE ?? 'matomo:5.14.0-apache', noPasswordResetVersion: guardPlugin.version });
  writeFileSync(output, `${JSON.stringify(bom, null, 2)}\n`);
}
