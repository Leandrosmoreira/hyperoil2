#!/bin/bash
# HyperOil v2 — VPS Setup Script
# Run as root on a fresh Ubuntu 22.04+ / Debian 12+ server

set -euo pipefail

echo "=== HyperOil v2 VPS Setup ==="

# Update system
apt-get update && apt-get upgrade -y

# Install Docker if not present
if ! command -v docker &>/dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Install Docker Compose plugin if not present
if ! docker compose version &>/dev/null; then
    echo "Installing Docker Compose plugin..."
    apt-get install -y docker-compose-plugin
fi

# Create application user
if ! id "hyperoil" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash hyperoil
    usermod -aG docker hyperoil
fi

# Create application directory
mkdir -p /opt/hyperoil/data/jsonl
chown -R hyperoil:hyperoil /opt/hyperoil

# Setup firewall (allow SSH + health endpoint)
if command -v ufw &>/dev/null; then
    ufw allow 22/tcp
    ufw allow 8080/tcp
    ufw --force enable
fi

# Install systemd service
if [ -f /opt/hyperoil/deploy/systemd/hyperoil.service ]; then
    cp /opt/hyperoil/deploy/systemd/hyperoil.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable hyperoil
    echo "Systemd service installed and enabled."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy project files to /opt/hyperoil/"
echo "  2. Create /opt/hyperoil/.env with your credentials"
echo "  3. Review /opt/hyperoil/config.yaml"
echo "  4. Build: cd /opt/hyperoil && docker compose build"
echo "  5. Start: systemctl start hyperoil"
echo "  6. Monitor: journalctl -u hyperoil -f"
echo "  7. Health: curl http://localhost:8080/health"
echo ""
