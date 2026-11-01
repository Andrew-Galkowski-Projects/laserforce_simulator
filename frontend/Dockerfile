# ANG-08 (ADR-0047): the nginx front door. Serves the built SPA at / and proxies
# only /api/, /admin/, /static/ and /media/ to Django at ${DJANGO_UPSTREAM}.

# Stage 1 -- build the SPA with the default base href (/)
FROM node:24-alpine AS build
WORKDIR /src
ENV NG_CLI_ANALYTICS=false
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx ng build --configuration production

# Stage 2 -- nginx; the official image renders /etc/nginx/templates/*.template
# with envsubst at start, filling only the variables set in the environment
FROM nginx:1.27-alpine
# NGINX_ENTRYPOINT_LOCAL_RESOLVERS: the entrypoint sets ${NGINX_LOCAL_RESOLVERS}
# from /etc/resolv.conf, for the template's per-request resolver.
ENV DJANGO_UPSTREAM=app:8000 \
    NGINX_PORT=80 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# Replace the image's default index.html / 50x.html with the SPA bundle.
RUN rm -rf /usr/share/nginx/html/*
COPY --from=build /src/dist/frontend/browser /usr/share/nginx/html
EXPOSE 80
