# Stage 1 — install Python dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY laserforce_simulator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2 — lean runtime image
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
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
