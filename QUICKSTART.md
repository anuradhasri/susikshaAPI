# Quick Start Guide

Get the backend running in 5 minutes!

## 1. Prerequisites

- Python 3.8+
- MySQL 5.7+
- Redis 6.0+
- pip

## 2. Clone & Setup

```bash
# Navigate to project directory
cd autism-backend

# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Edit .env with your database and Redis credentials
```

## 3. Database Setup

```bash
# Create MySQL database
mysql -u root -p
CREATE DATABASE autism_therapy;
EXIT;

# Initialize database with default data
python init_db.py
```

You should see:
```
✓ Database tables created
✓ Default roles created
✓ Default regions created
✓ Admin user created
✓ Sample patients created
```

## 4. Start Development Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 5. Access the Application

- **API Swagger Documentation**: http://localhost:8000/api/docs
- **Alternative ReDoc**: http://localhost:8000/api/redoc
- **Health Check**: http://localhost:8000/health

## 6. Quick Login Test

Open the Swagger UI and:

1. Click "Authorize" button
2. Use one of the sample credentials:
   - **Admin**: username=admin, password=admin123
   - **Therapist**: username=therapist1, password=therapist123
   - **Front Office**: username=frontoffice1, password=frontoffice123

## 7. Try Sample Endpoints

### List Patients
```bash
curl -X GET "http://localhost:8000/api/v1/patients" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Create Patient
```bash
curl -X POST "http://localhost:8000/api/v1/patients" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "John",
    "last_name": "Doe",
    "date_of_birth": "2010-01-15",
    "email": "john@example.com",
    "phone": "555-1234",
    "diagnosis": "ASD Level 1",
    "region_id": 1
  }'
```

## Production Deployment

### Using Gunicorn (10 workers)

```bash
gunicorn -c gunicorn_conf.py app.main:app
```

### Using Docker (Optional)

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["gunicorn", "-c", "gunicorn_conf.py", "app.main:app"]
```

### Using systemd (Linux)

Create `/etc/systemd/system/autism-backend.service`:

```ini
[Unit]
Description=Autism Therapy Backend
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/autism-backend
ExecStart=/usr/bin/gunicorn -c gunicorn_conf.py app.main:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable autism-backend
sudo systemctl start autism-backend
```

## Troubleshooting

### Database Connection Error
- Ensure MySQL is running: `mysql -u root -p`
- Check DATABASE_URL in .env
- Verify credentials

### Redis Connection Error
- Ensure Redis is running: `redis-cli ping` (should return PONG)
- Check REDIS_URL in .env

### Port Already in Use
```bash
# Change port in Uvicorn command
uvicorn app.main:app --reload --port 8001
```

### Import Errors
```bash
# Verify all dependencies installed
pip install -r requirements.txt --upgrade
```

## Next Steps

1. Read full documentation in [README.md](README.md)
2. Explore API at http://localhost:8000/api/docs
3. Review example workflows in [EXAMPLES.md](EXAMPLES.md)
4. Check deployment guide in [DEPLOYMENT.md](DEPLOYMENT.md)

## Support

- Check logs: `tail -f logs/app.log`
- Verify database: `mysql -u root -p autism_therapy`
- Test API connectivity: `curl http://localhost:8000/health`
