import logging
import json
from datetime import datetime
from app.core.config import get_settings

settings = get_settings()


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in ["name", "msg", "args", "created", "filename", "funcName", 
                               "levelname", "levelno", "lineno", "module", "msecs", 
                               "message", "pathname", "process", "processName", "relativeCreated", 
                               "thread", "threadName", "exc_info", "exc_text", "stack_info"]:
                    log_obj[key] = value
        
        return json.dumps(log_obj)


def setup_logging(name: str):
    """Setup logging configuration"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))
    
    # Console handler
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    
    return logger
