#!/bin/bash
# USPA Judge Test - Oracle Cloud Deployment Script
# Run this on your Oracle Cloud VM after SSH'ing in

set -e  # Exit on any error

echo "=========================================="
echo "  USPA Judge Test - Deployment Script"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}Please run without sudo. Script will ask for sudo when needed.${NC}"
    exit 1
fi

# Get environment variables from user
echo ""
read -p "SECRET_KEY (or press Enter for auto-generated): " SECRET_KEY

if [ -z "$SECRET_KEY" ]; then
    SECRET_KEY=$(openssl rand -hex 32)
    echo -e "${GREEN}Generated SECRET_KEY: $SECRET_KEY${NC}"
fi

echo ""
echo -e "${GREEN}Step 1: Updating system packages...${NC}"
sudo apt update && sudo apt upgrade -y

echo ""
echo -e "${GREEN}Step 2: Installing dependencies...${NC}"
sudo apt install -y python3-pip python3-venv nginx git

echo ""
echo -e "${GREEN}Step 3: Cloning repository...${NC}"
cd ~
if [ -d "judgetest" ]; then
    echo "Directory exists, pulling latest..."
    cd judgetest
    git pull
else
    git clone https://github.com/kdrivas1989/judgetest.git
    cd judgetest
fi

echo ""
echo -e "${GREEN}Step 4: Setting up Python virtual environment...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo -e "${GREEN}Step 5: Creating systemd service...${NC}"
sudo tee /etc/systemd/system/judgetest.service > /dev/null <<EOF
[Unit]
Description=USPA Judge Test Flask Application
After=network.target

[Service]
User=$USER
Group=$USER
WorkingDirectory=/home/$USER/judgetest
Environment="PATH=/home/$USER/judgetest/venv/bin"
Environment="SECRET_KEY=$SECRET_KEY"
ExecStart=/home/$USER/judgetest/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:5000 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo -e "${GREEN}Step 6: Configuring Nginx...${NC}"
sudo tee /etc/nginx/sites-available/judgetest > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 90;
    }

    location /static {
        alias /home/ubuntu/judgetest/static;
        expires 30d;
    }
}
EOF

# Enable the site
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/judgetest /etc/nginx/sites-enabled/

# Test nginx config
sudo nginx -t

echo ""
echo -e "${GREEN}Step 7: Opening firewall ports...${NC}"
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save 2>/dev/null || true

echo ""
echo -e "${GREEN}Step 8: Starting services...${NC}"
sudo systemctl daemon-reload
sudo systemctl enable judgetest
sudo systemctl restart judgetest
sudo systemctl restart nginx

echo ""
echo -e "${GREEN}Step 9: Checking status...${NC}"
sleep 2
sudo systemctl status judgetest --no-pager

echo ""
echo "=========================================="
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "=========================================="
echo ""
PUBLIC_IP=$(curl -s ifconfig.me)
echo -e "Your app is now live at: ${GREEN}http://$PUBLIC_IP${NC}"
echo ""
echo "Useful commands:"
echo "  - View logs:     sudo journalctl -u judgetest -f"
echo "  - Restart app:   sudo systemctl restart judgetest"
echo "  - Stop app:      sudo systemctl stop judgetest"
echo "  - App status:    sudo systemctl status judgetest"
echo ""
