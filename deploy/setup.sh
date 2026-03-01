#!/bin/bash
# EC2 Ubuntu 서버 초기 설정 스크립트
# 사용법: sudo bash setup.sh

set -e

echo "=== 1. 시스템 업데이트 ==="
apt update && apt upgrade -y

echo "=== 2. 필수 패키지 설치 ==="
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git

echo "=== 3. 프로젝트 디렉토리 생성 ==="
mkdir -p /home/ubuntu/snskeep/data
chown -R ubuntu:ubuntu /home/ubuntu/snskeep

echo "=== 4. Nginx 설정 ==="
# 기본 설정 제거
rm -f /etc/nginx/sites-enabled/default

echo "=== 5. 방화벽 설정 ==="
ufw allow 22
ufw allow 80
ufw allow 443
ufw --force enable

echo ""
echo "========================================="
echo "  초기 설정 완료!"
echo "========================================="
echo ""
echo "다음 단계:"
echo "  1. 프로젝트 파일을 /home/ubuntu/snskeep/ 에 업로드"
echo "  2. sudo bash deploy.sh YOUR_DOMAIN.com 실행"
echo ""
