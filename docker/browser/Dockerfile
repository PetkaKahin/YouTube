# Frontend browser checks use the locally built Node image and no service secrets.
FROM youtube-node:latest AS base
USER root
RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends chromium firefox-esr libavcodec59 ca-certificates fonts-liberation >/dev/null \
    && rm -rf /var/lib/apt/lists/*

FROM base AS dev
USER node
ENTRYPOINT ["node"]

FROM base AS prod
USER node
ENTRYPOINT ["node"]
