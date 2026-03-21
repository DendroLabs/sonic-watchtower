FROM python:3.11-slim-bookworm

LABEL maintainer="watchtower"
LABEL description="Watchtower: Distributed read-only network observer for SONiC switches"

WORKDIR /opt/watchtower

# Install dependencies first for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY watchtower/ watchtower/
COPY scripts/ scripts/
COPY watchtower.yml.example watchtower.yml.example

# Create runtime directories
RUN mkdir -p /var/lib/watchtower /var/run/watchtower

# Default configuration
ENV WATCHTOWER_CONFIG=/opt/watchtower/watchtower.yml

ENTRYPOINT ["python", "-m", "watchtower.main"]
