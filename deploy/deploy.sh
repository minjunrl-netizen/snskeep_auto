#!/bin/bash
# 앱 배포 스크립트
# 사용법: sudo bash deploy.sh YOUR_DOMAIN.com

set -e

DOMAIN=$1

if [ -z "$DOMAIN" ]; then
    echo "사용법: sudo bash deploy.sh YOUR_DOMAIN.com"
    exit 1
fi

APP_DIR="/home/ubuntu/snskeep"

echo "=== 도메인: $DOMAIN ==="

echo "=== 1. Python 가상환경 설정 ==="
cd $APP_DIR
sudo -u ubuntu python3 -m venv venv
sudo -u ubuntu $APP_DIR/venv/bin/pip install --upgrade pip
sudo -u ubuntu $APP_DIR/venv/bin/pip install -r requirements.txt

echo "=== 2. data 디렉토리 확인 ==="
mkdir -p $APP_DIR/data
chown -R ubuntu:ubuntu $APP_DIR/data

echo "=== 3. Nginx 설정 ==="
# 도메인 치환하여 Nginx 설정 배포
sed "s/YOUR_DOMAIN.com/$DOMAIN/g" $APP_DIR/deploy/nginx.conf > /etc/nginx/sites-available/snskeep
ln -sf /etc/nginx/sites-available/snskeep /etc/nginx/sites-enabled/snskeep
nginx -t

echo "=== 4. SSL 인증서 발급 (Let's Encrypt) ==="
# 먼저 HTTP만으로 Nginx 시작 (SSL 없이)
cat > /etc/nginx/sites-available/snskeep << TMPEOF
server {
    listen 80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
TMPEOF

mkdir -p /var/www/certbot
nginx -t && systemctl restart nginx

# Certbot으로 SSL 발급 + Nginx 자동 설정
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN --redirect

echo "=== 5. systemd 서비스 등록 ==="
cp $APP_DIR/deploy/snskeep.service /etc/systemd/system/snskeep.service
systemctl daemon-reload
systemctl enable snskeep
systemctl start snskeep

echo "=== 6. .env 업데이트 알림 ==="
echo ""
echo "========================================="
echo "  배포 완료!"
echo "========================================="
echo ""
echo "중요: .env 파일에서 아래 항목을 수정하세요:"
echo "  CAFE24_REDIRECT_URI=https://$DOMAIN/oauth/callback"
echo "  FLASK_DEBUG=false"
echo "  FLASK_SECRET_KEY=(랜덤 문자열로 변경)"
echo ""
echo "수정 후: sudo systemctl restart snskeep"
echo ""
echo "서버 상태 확인: sudo systemctl status snskeep"
echo "로그 확인: sudo journalctl -u snskeep -f"
echo "사이트: https://$DOMAIN/admin/"
echo ""
