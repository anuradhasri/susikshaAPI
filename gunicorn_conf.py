import multiprocessing

# Server Socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = 10  # Number of worker processes
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 30
keepalive = 2

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "autism-therapy-backend"

# SSL
# keyfile = None
# certfile = None
# ssl_version = "TLSv1_2"
# cert_reqs = 0
# ca_certs = None
# suppress_ragged_eof = True

# SSL Defaults
# do_handshake_on_connect = True
# suppress_ragged_eof = True
# ssl_options = None

# Server hooks
preload_app = True


def on_starting(server):
    """Hook run on server startup"""
    pass


def when_ready(server):
    """Hook run when Gunicorn is ready to accept requests"""
    pass


def on_exit(server):
    """Hook run on server shutdown"""
    pass


# Application setup
raw_env = [
    "PYTHONUNBUFFERED=1"
]
