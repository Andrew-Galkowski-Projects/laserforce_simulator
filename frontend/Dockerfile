# ANG-07 (ADR-0046): the nginx front door. Serves the built SPA at /app/ and
# proxies every other path to Django at ${DJANGO_UPSTREAM}.

# Stage 1 -- build the SPA with the /app/ base href
FROM node:24-alpine AS build
WORKDIR /src
ENV NG_CLI_ANALYTICS=false
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx ng build --configuration production --base-href /app/

# Stage 2 -- nginx; the official image renders /etc/nginx/templates/*.template
# with envsubst at start, filling only the variables set in the environment
FROM nginx:1.27-alpine
ENV DJANGO_UPSTREAM=app:8000 \
    NGINX_PORT=80
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist/frontend/browser /usr/share/nginx/html/app
EXPOSE 80
