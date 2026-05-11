# Production Deployment Guide

Complete guide for deploying the Autism Therapy Management System backend to production.

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Environment Setup](#environment-setup)
3. [Gunicorn + Nginx](#gunicorn--nginx)
4. [Docker Deployment](#docker-deployment)
5. [Database Migration](#database-migration)
6. [Security Hardening](#security-hardening)
7. [Monitoring & Logging](#monitoring--logging)
8. [Backup & Recovery](#backup--recovery)

## Pre-Deployment Checklist

- [ ] Change `SECRET_KEY` in `.env` to a strong random string
- [ ] Update `DATABASE_URL` to production database
- [ ] Update `REDIS_URL` to production Redis instance
- [ ] Set `DEBUG=False` in `.env`
- [ ] Configure `CORS_ORIGINS` for your domain
- [ ] Review and adjust `ACCESS_TOKEN_EXPIRE_MINUTES`
- [ ] Set up SSL/TLS certificates
- [ ] Configure email for notifications (if needed)
- [ ] Set up monitoring and alerting
- [ ] Test with production database

## Environment Setup

### 1. Create Production .env

```bash
cp .env.example .env.production
nano .env.production
```

```ini
# Application
APP_NAME="Autism Therapy Management System"
APP_VERSION="1.0.0"
DEBUG=False

# Database (Production)
DATABASE_URL=mysql+pymysql://user:password@prod-db.example.com:3306/autism_therapy
SQLALCHEMY_ECHO=False

# Redis (Production)
REDIS_URL=redis://:password@prod-redis.example.com:6379/0

# JWT
SECRET_KEY=your-super-secret-production-key-min-32-chars-very-random
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# CORS
CORS_ORIGINS=["https://app.example.com", "https://admin.example.com"]

# Logging
LOG_LEVEL=INFO

# Email (Optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

### 2. Generate Secure Secret Key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output and paste into `SECRET_KEY` in `.env.production`

## Gunicorn + Nginx

### 1. Install Gunicorn

```bash
pip install gunicorn
```

### 2. Production gunicorn_conf.py

Edit `gunicorn_conf.py`:

```python
import multiprocessing

# Server Socket
bind = "127.0.0.1:8000"  # Don't expose directly to internet
backlog = 2048

# Worker processes
workers = (multiprocessing.cpu_count() * 2) + 1  # Dynamic worker count
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 30
keepalive = 2

# Logging
accesslog = "/var/log/autism-backend/access.log"
errorlog = "/var/log/autism-backend/error.log"
loglevel = "info"

# Process naming
proc_name = "autism-therapy-backend"

# Preload app for memory efficiency
preload_app = True
```

### 3. Nginx Configuration

Create `/etc/nginx/sites-available/autism-backend`:

```nginx
upstream autism_backend {
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
    keepalive 32;
}

server {
    listen 80;
    server_name api.example.com;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;
    
    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    # Logging
    access_log /var/log/nginx/autism-backend-access.log;
    error_log /var/log/nginx/autism-backend-error.log;
    
    # Client limits
    client_max_body_size 10M;
    
    # Proxy settings
    location / {
        proxy_pass http://autism_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_request_buffering off;
    }
    
    # Static files (if needed)
    location /static/ {
        alias /var/www/autism-backend/static/;
        expires 30d;
    }
}
```

Enable site:
```bash
sudo ln -s /etc/nginx/sites-available/autism-backend /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Systemd Service

Create `/etc/systemd/system/autism-backend.service`:

```ini
[Unit]
Description=Autism Therapy Management Backend
After=network.target mysql.service redis.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/var/www/autism-backend
Environment="PATH=/var/www/autism-backend/venv/bin"
ExecStart=/var/www/autism-backend/venv/bin/gunicorn \
    -c gunicorn_conf.py \
    --env-file .env.production \
    app.main:app
Restart=on-failure
RestartSec=5s

# Security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
```

Start service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable autism-backend
sudo systemctl start autism-backend
sudo systemctl status autism-backend
```

## Docker Deployment

### 1. Create Dockerfile

```dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run gunicorn
CMD ["gunicorn", "-c", "gunicorn_conf.py", "app.main:app"]
```

### 2. Docker Compose for Full Stack

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  db:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
      MYSQL_DATABASE: autism_therapy
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASSWORD}
    volumes:
      - db_data:/var/lib/mysql
    ports:
      - "3306:3306"
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build: .
    environment:
      DATABASE_URL: mysql+pymysql://${DB_USER}:${DB_PASSWORD}@db:3306/autism_therapy
      REDIS_URL: redis://redis:6379/0
      SECRET_KEY: ${SECRET_KEY}
      DEBUG: "False"
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  db_data:
  redis_data:
```

Deploy:
```bash
docker-compose -f docker-compose.yml up -d
docker-compose logs -f backend
```

## Database Migration

### 1. Backup Current Database

```bash
mysqldump -u root -p autism_therapy > backup_$(date +%Y%m%d_%H%M%S).sql
```

### 2. Run Database Initialization

```bash
# On production server
python init_db.py
```

### 3. Restore from Backup (if needed)

```bash
mysql -u root -p autism_therapy < backup_20260504_120000.sql
```

## Security Hardening

### 1. Firewall Rules

```bash
# Allow only necessary ports
sudo ufw enable
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw deny 8000/tcp   # Block direct Gunicorn access
sudo ufw deny 6379/tcp   # Block direct Redis access
sudo ufw deny 3306/tcp   # Block direct MySQL access
```

### 2. Database Security

```bash
# Create database user with limited privileges
mysql -u root -p
CREATE USER 'app_user'@'localhost' IDENTIFIED BY 'strong_password';
GRANT SELECT, INSERT, UPDATE, DELETE ON autism_therapy.* TO 'app_user'@'localhost';
FLUSH PRIVILEGES;
```

### 3. SSL/TLS Certificate

```bash
# Using Let's Encrypt
sudo apt-get install certbot python3-certbot-nginx
sudo certbot certonly --nginx -d api.example.com
sudo certbot renew --dry-run  # Test renewal
```

### 4. Environment File Security

```bash
chmod 600 .env.production
sudo chown www-data:www-data .env.production
```

## Monitoring & Logging

### 1. Application Logging

```bash
# Create log directory
sudo mkdir -p /var/log/autism-backend
sudo chown www-data:www-data /var/log/autism-backend
```

### 2. System Monitoring

```bash
# Install monitoring tools
sudo apt-get install htop iotop nethogs

# Monitor process
watch -n 1 'ps aux | grep gunicorn'
```

### 3. Log Aggregation (ELK Stack - Optional)

```bash
# Install Elasticsearch, Logstash, Kibana
# Configure Logstash to parse JSON logs
# Set up dashboards in Kibana
```

### 4. Health Checks

```bash
# Automated health monitoring
*/5 * * * * curl -f http://localhost:8000/health || systemctl restart autism-backend
```

## Backup & Recovery

### 1. Automated Database Backup

Create `/usr/local/bin/backup-autism-db.sh`:

```bash
#!/bin/bash

BACKUP_DIR="/backups/autism-therapy"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/autism_therapy_$DATE.sql"

mkdir -p $BACKUP_DIR

mysqldump -u $DB_USER -p$DB_PASSWORD autism_therapy > $BACKUP_FILE

# Keep only last 30 days
find $BACKUP_DIR -type f -mtime +30 -delete

# Upload to S3 (optional)
aws s3 cp $BACKUP_FILE s3://backups-bucket/autism-therapy/

echo "Backup completed: $BACKUP_FILE"
```

Schedule with cron:
```bash
# Backup every day at 2 AM
0 2 * * * /usr/local/bin/backup-autism-db.sh
```

### 2. Redis Persistence

In `gunicorn_conf.py`, ensure Redis is configured for persistence:

```bash
# Redis should have appendonly.aof enabled
redis-cli CONFIG GET appendonly
redis-cli CONFIG SET appendonly yes
```

### 3. Recovery Procedure

```bash
# 1. Stop application
sudo systemctl stop autism-backend

# 2. Restore database
mysql -u root -p autism_therapy < backup_20260504_120000.sql

# 3. Verify data
mysql -u root -p -e "SELECT COUNT(*) FROM autism_therapy.patients;"

# 4. Start application
sudo systemctl start autism-backend

# 5. Verify health
curl http://localhost:8000/health
```

## Performance Tuning

### 1. Database Optimization

```sql
-- Check slow queries
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 2;

-- Run EXPLAIN on slow queries
EXPLAIN SELECT * FROM patients WHERE region_id = 1;
```

### 2. Redis Optimization

```bash
# Adjust Redis memory
redis-cli CONFIG GET maxmemory
redis-cli CONFIG SET maxmemory 2gb

# Set eviction policy
redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

### 3. Gunicorn Tuning

```python
# Adjust worker count based on CPU
workers = (multiprocessing.cpu_count() * 2) + 1

# Adjust timeout based on slowest endpoint
timeout = 60

# Connection keep-alive
keepalive = 5
```

## Troubleshooting Production Issues

### 1. High Memory Usage

```bash
# Check memory
free -h

# Check Gunicorn processes
ps aux | grep gunicorn

# Restart application
sudo systemctl restart autism-backend
```

### 2. Database Connection Issues

```bash
# Test connection
mysql -u app_user -p -h localhost -e "SELECT 1"

# Check pool settings
# Increase pool_size and max_overflow in database.py
```

### 3. Redis Connection Issues

```bash
# Test Redis
redis-cli ping

# Check Redis logs
tail -f /var/log/redis/redis-server.log
```

### 4. Nginx Errors

```bash
# Check Nginx syntax
sudo nginx -t

# Check Nginx logs
tail -f /var/log/nginx/autism-backend-error.log
```

## Rollback Procedure

```bash
# 1. Identify last known good version
git log --oneline | head -5

# 2. Checkout previous version
git checkout <commit-hash>

# 3. Rebuild and restart
pip install -r requirements.txt
sudo systemctl restart autism-backend

# 4. Verify
curl http://localhost:8000/health
```

---

## Support & Documentation

- [Main README](README.md)
- [Quick Start Guide](QUICKSTART.md)
- [API Examples](EXAMPLES.md)
- [Nginx Docs](https://nginx.org/en/docs/)
- [Gunicorn Docs](https://docs.gunicorn.org/)
