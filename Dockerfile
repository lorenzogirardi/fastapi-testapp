FROM python:3.14.7-slim

ARG UID=10001
ARG GID=10001
ARG USER=pytbak

# Install system deps for network tools + healthcheck
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        iputils-ping traceroute dnsutils curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid $GID $USER && \
    useradd --uid $UID --gid $GID --no-create-home --shell /bin/false $USER

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY app/ /app/app/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/alembic.ini
COPY pyproject.toml /app/

RUN chown -R $USER:$USER /app
USER $USER

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/mgmt/ready || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
