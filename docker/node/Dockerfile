# syntax=docker/dockerfile:1
ARG NODE_IMAGE

FROM ${NODE_IMAGE} AS base
ENV NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /app
RUN chown node:node /app
USER node

FROM base AS dev
COPY --chown=node:node frontend/package.json frontend/package-lock.json ./
RUN npm ci \
    && sha256sum package-lock.json | cut -d ' ' -f 1 > node_modules/.lock-digest
COPY --chown=node:node frontend/ ./
COPY --chmod=755 docker/node/dev-entrypoint.sh /usr/local/bin/dev-entrypoint
ENTRYPOINT ["dev-entrypoint"]
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM base AS prod
COPY --chown=node:node frontend/package.json frontend/package-lock.json ./
COPY --chown=node:node frontend/ ./
RUN npm ci \
    && npm run build \
    && rm -rf node_modules \
    && npm cache clean --force
CMD ["node", "-e", "require('node:fs').accessSync('/app/dist/index.html')"]
