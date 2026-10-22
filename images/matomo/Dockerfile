# syntax=docker/dockerfile:1
# Matomo with the LoginOIDC plugin for login via Keycloak, plus NoPasswordReset.
# Without MATOMO_OIDC_* variables it behaves like the official image (password
# login only). The image workflow passes both base images pinned by digest.
ARG NODE_BASE_IMAGE=node:24.22.1-bookworm-slim
ARG RUNTIME_BASE_IMAGE=matomo:5.14.0-apache

# Writes the image SBOM, the release evidence every image carries.
FROM ${NODE_BASE_IMAGE} AS sbom
ARG RUNTIME_BASE_IMAGE
ARG BUILD_ID=local
ARG SOURCE_COMMIT=local
WORKDIR /sbom
COPY sbom.mjs ./
COPY plugins/NoPasswordReset/plugin.json plugins/NoPasswordReset/plugin.json
RUN node sbom.mjs SBOM.cdx.json

FROM ${RUNTIME_BASE_IMAGE}
ADD --checksum=sha256:265433ec2cc526b627e3bb124c76c3521309d2a8becce06db95d89ce65a60bb2 \
  https://github.com/dominik-th/matomo-plugin-LoginOIDC/archive/refs/tags/5.0.0.tar.gz /tmp/loginoidc.tar.gz
RUN mkdir -p /opt/matomo-plugins/LoginOIDC \
  && tar xzf /tmp/loginoidc.tar.gz -C /opt/matomo-plugins/LoginOIDC --strip-components=1 \
  && rm /tmp/loginoidc.tar.gz
COPY plugins/NoPasswordReset /opt/matomo-plugins/NoPasswordReset
COPY common.config.ini.php config.php /opt/matomo-config/
COPY apache-logging.conf /etc/apache2/conf-enabled/dez-logging.conf
RUN sed -i 's|^\(\s*CustomLog .*access\.log\) combined$|\1 dez|' /etc/apache2/sites-available/000-default.conf \
  && grep -q 'CustomLog .*access\.log dez$' /etc/apache2/sites-available/000-default.conf
COPY --chmod=755 entrypoint.sh /usr/local/bin/matomo-entrypoint.sh
COPY --from=sbom /sbom/SBOM.cdx.json /usr/share/regensburg/SBOM.cdx.json
ENTRYPOINT ["matomo-entrypoint.sh"]
CMD ["apache2-foreground"]
ARG BUILD_ID=local
ARG SOURCE_COMMIT=local
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/csi-FOXBYTE/Regensburg_DigitalerEnergieZwilling_digital-energy-twin_addon" \
      org.opencontainers.image.version="build-${BUILD_ID}" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}"
