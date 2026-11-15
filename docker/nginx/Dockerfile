# syntax=docker/dockerfile:1
ARG NGINX_IMAGE

FROM ${NGINX_IMAGE} AS base
COPY docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx/api.conf /etc/nginx/snippets/api.conf
COPY docker/nginx/media-errors.conf /etc/nginx/snippets/media-errors.conf
COPY docker/nginx/media-range-map.conf /etc/nginx/snippets/media-range-map.conf
USER nginx
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]

FROM base AS dev
COPY docker/nginx/dev.conf /etc/nginx/conf.d/default.conf

FROM base AS prod
COPY docker/nginx/prod.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend /app/dist/ /usr/share/nginx/html/
