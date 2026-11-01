# Stage 1 — build the Angular SPA (ANG-10, ADR-0049: Django serves it)
FROM node:24-alpine AS spa
WORKDIR /src
ENV NG_CLI_ANALYTICS=false
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npx ng build --configuration production

# Stage 2 — install Python dependencies
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY laserforce_simulator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3 — lean runtime image
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# The SPA lives outside /app: the dev compose stack mounts the repo over /app.
# Pre-compressed (.gz / .br) so WhiteNoise serves compressed bundles.
COPY --from=spa /src/dist/frontend/browser /srv/spa
RUN python -m whitenoise.compress /srv/spa
ENV SPA_ROOT=/srv/spa
COPY . .
RUN SECRET_KEY=build-only-not-a-real-secret \
    DATABASE_URL=sqlite:////tmp/build.db \
    DJANGO_SETTINGS_MODULE=laserforce_simulator.settings \
    python laserforce_simulator/manage.py collectstatic --noinput
RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "laserforce_simulator.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--chdir", "laserforce_simulator", \
     "--workers", "3", \
     "--timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
