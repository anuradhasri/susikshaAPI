import time
import json
import logging
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.core.security import decode_token
from app.utils.logger import setup_logging

logger = setup_logging(__name__)


class RegionAccessMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce region-based access control
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip middleware for auth endpoints
        if request.url.path in ["/api/v1/auth/login", "/api/v1/auth/register"]:
            return await call_next(request)
        
        # Get token from header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return await call_next(request)
        
        token = auth_header.replace("Bearer ", "")
        token_data = decode_token(token)
        
        if not token_data:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or expired token"}
            )
        
        # Store token data in request state for later use
        request.state.user_id = token_data.get("user_id")
        request.state.region_id = token_data.get("region_id")
        request.state.roles = token_data.get("roles", [])
        
        response = await call_next(request)
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log all requests and responses
    """
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # Log request
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown"
            }
        )
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            # Log response
            logger.info(
                f"Response: {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time": process_time
                }
            )
            
            return response
        except Exception as exc:
            process_time = time.time() - start_time
            logger.error(
                f"Error: {request.method} {request.url.path} - {str(exc)}",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "process_time": process_time,
                    "error": str(exc)
                }
            )
            raise


class QueryFilteringMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add region filtering to queries
    This stores the user's region_id for use in query filtering
    """
    
    async def dispatch(self, request: Request, call_next):
        # Attach region_id to request for query filtering
        if hasattr(request.state, "region_id"):
            request.state.filter_region_id = request.state.region_id
        
        response = await call_next(request)
        return response
