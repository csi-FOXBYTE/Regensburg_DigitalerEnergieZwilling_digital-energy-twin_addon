#!/usr/bin/env bash
# Runs the given specs with the browser on the same Docker network as Matomo.
set -euo pipefail

mkdir -p /runner/tests
cp /installer/*.ts /runner/tests/
cp /installer/playwright.config.mjs /runner/
cd /runner
if [ ! -d node_modules/@playwright/test ]; then
  echo '{"name":"dez-matomo-installer","private":true}' > package.json
  npm install --silent --no-audit --no-fund @playwright/test@1.62.1
fi
npx playwright test "$@"
