"""Gunicorn WSGI 진입점"""
from app import create_app, start_scheduler

app = create_app()
scheduler = start_scheduler(app)
