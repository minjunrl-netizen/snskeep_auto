# Gunicorn 설정
bind = "127.0.0.1:5000"
workers = 1  # 스케줄러 중복 방지를 위해 워커 1개만
threads = 2
timeout = 120
accesslog = "-"
errorlog = "-"
loglevel = "info"
