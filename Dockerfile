FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Sensible defaults so no .env file is needed. Credentials are entered in the
# setup page and stored encrypted in the database; any of these (or any
# provider setting) can still be overridden with an environment variable.
ENV RABBITMQ_URL=amqp://guest:guest@127.0.0.1:5672/%2F \
    TEMPLATES_DIR=/app/app/templates \
    SMS_TEMPLATE_DIR=/app/app/templates/sms \
    EMAIL_TEMPLATE_DIR=/app/app/templates/email \
    GATEWAY_BASE_URL=http://localhost:8010 \
    PUBLIC_MCP_URL=http://localhost:8010/mcp

# RabbitMQ via Debian's own bookworm repo (not a third-party PPA) — deliberately
# avoids depending on a third-party apt repo/signing-key URL that could go
# stale and silently break `docker build` for end users. This installs
# whatever RabbitMQ + Erlang version ships in Debian bookworm, which is
# slightly behind the latest upstream RabbitMQ release but is a stable,
# officially-packaged, always-resolvable source.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates rabbitmq-server \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir supervisor

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# supervisord runs as root, but Debian's rabbitmq-server drops to the
# `rabbitmq` system user, so /data/rabbitmq must be owned by it (a named
# volume inherits this ownership on first creation).
# RABBITMQ_MNESIA_BASE/RABBITMQ_LOG_BASE (set in supervisord.conf) point
# RabbitMQ at these paths explicitly rather than relying on whatever
# default paths/ownership the Debian package assumes.
RUN chmod +x /app/docker/wait-for-rabbitmq.sh /app/docker/healthcheck.sh \
    && mkdir -p /data/rabbitmq/mnesia /data/rabbitmq/log /app/data \
    && chown -R rabbitmq:rabbitmq /data/rabbitmq \
    && rabbitmq-plugins enable --offline rabbitmq_management

EXPOSE 8000 15672

HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=5 \
    CMD /app/docker/healthcheck.sh

ENTRYPOINT ["supervisord", "-c", "/app/docker/supervisord.conf"]
