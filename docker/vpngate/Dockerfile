FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIMILI_SERVICE_MODE=vpngate \
    VPNGATE_DATA_DIR=/data \
    UI_HOST=0.0.0.0 \
    UI_PORT=8787 \
    LOCAL_PROXY_HOST=0.0.0.0 \
    LOCAL_PROXY_PORT=7928

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        iproute2 \
        iptables \
        openvpn \
        procps \
        psmisc \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Automatically detect system architecture and download/install Xray Core
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then XRAY_ARCH="64"; \
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then XRAY_ARCH="arm64-v8a"; \
    elif [ "$ARCH" = "armv7l" ]; then XRAY_ARCH="arm32-v7a"; \
    else XRAY_ARCH="64"; fi && \
    curl -L "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip" -o xray.zip \
    && unzip xray.zip xray -d /usr/local/bin/ \
    && rm xray.zip \
    && chmod +x /usr/local/bin/xray

WORKDIR /app

COPY backend /app/backend
COPY proxy /app/proxy
COPY utils /app/utils
COPY web /app/web
COPY cli /app/cli
COPY docker/vpngate/entrypoint.sh /usr/local/bin/aimili-vpngate-entrypoint

RUN chmod +x /usr/local/bin/aimili-vpngate-entrypoint \
    && mkdir -p /data \
    && echo '#!/bin/bash\ncd /app\nexec python cli/menu.py "$@"' > /usr/bin/ml \
    && chmod +x /usr/bin/ml

VOLUME ["/data"]
EXPOSE 8787 7928

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 7928), 3); s.close()"

ENTRYPOINT ["/usr/local/bin/aimili-vpngate-entrypoint"]
CMD ["python", "/app/backend/vpngate_manager.py"]
