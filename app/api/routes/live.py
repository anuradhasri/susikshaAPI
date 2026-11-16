"""Authenticate WebSockets in the first message, never in a logged URL."""
import asyncio
import json
import time
from contextlib import suppress
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import decode_token
from app.models.models import User, UserRole, Role, UserRegionMapping
from app.services.realtime import LivePrincipal, hub

router = APIRouter(prefix='/api/v1', tags=['live updates'])
STAFF_TOPICS = frozenset({'appointments', 'payouts', 'notifications', 'payment-alerts',
                          'payments', 'therapists', 'patients', 'programs', 'regions', 'access'})
THERAPIST_TOPICS = frozenset({'appointments', 'payouts', 'notifications', 'access'})


def authenticate_live(token, region_id):
    payload = decode_token(token)
    if not payload or payload.get('type') == 'refresh' or not payload.get('user_id'):
        raise ValueError('invalid token')
    expires_at = float(payload.get('exp') or 0)
    if expires_at <= time.time():
        raise ValueError('expired token')
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.id == payload['user_id'], User.deleted_at.is_(None)))
        if not user or not user.is_active:
            raise ValueError('inactive user')
        roles = {str(name).lower().replace('-', '_').replace(' ', '_') for name in db.scalars(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(
                UserRole.user_id == user.id, UserRole.deleted_at.is_(None), Role.deleted_at.is_(None)))}
        regions = set(db.scalars(select(UserRegionMapping.regionid).where(UserRegionMapping.userid == user.id)))
        admin = 'admin' in roles
        if region_id is not None:
            if isinstance(region_id, bool) or not isinstance(region_id, int) or region_id <= 0:
                raise PermissionError('invalid centre')
            if not admin and region_id not in regions:
                raise PermissionError('centre not allowed')
            regions = {region_id}
        staff = bool(roles & {'admin', 'central_head', 'centralhead', 'front_office', 'frontoffice', 'front_officer'})
        topics = STAFF_TOPICS if staff else THERAPIST_TOPICS
        return LivePrincipal(user.id, frozenset(regions), topics, expires_at, admin and region_id is None)


@router.websocket('/live')
async def live_updates(websocket: WebSocket):
    origin = websocket.headers.get('origin')
    if origin and origin not in get_settings().CORS_ORIGINS:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    connection = None
    sender = None
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        if len(raw) > 16384:
            await websocket.close(code=4400)
            return
        auth = json.loads(raw)
        if not isinstance(auth, dict) or auth.get('type') != 'authenticate':
            await websocket.close(code=4401)
            return
        token = auth.get('token')
        client_id = auth.get('client_id')
        if not isinstance(token, str) or not isinstance(client_id, str) or not 8 <= len(client_id) <= 128:
            await websocket.close(code=4401)
            return
        principal = await run_in_threadpool(authenticate_live, token, auth.get('region_id'))
        connection = hub.connect(principal, client_id)
        await websocket.send_json({'type': 'ready'})

        async def send_messages():
            while True:
                message = await connection.queue.get()
                if message['type'] in {'close', 'unauthorized'}:
                    await websocket.close(code=4401 if message['type'] == 'unauthorized' else 1013)
                    return
                await websocket.send_json(message)
        sender = asyncio.create_task(send_messages())
        checked_at = time.monotonic()
        last_pong = checked_at
        while True:
            if connection.principal.expires_at <= time.time():
                await websocket.close(code=4401)
                return
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=25)
                if len(raw) > 512:
                    await websocket.close(code=4400)
                    return
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get('type') != 'pong':
                    await websocket.close(code=4400)
                    return
                last_pong = time.monotonic()
            except asyncio.TimeoutError:
                if time.monotonic() - last_pong > 75:
                    await websocket.close(code=1001)
                    return
                hub._enqueue(connection, {'type': 'ping'})
            if time.monotonic() - checked_at > 60:
                connection.principal = await run_in_threadpool(authenticate_live, token, auth.get('region_id'))
                checked_at = time.monotonic()
    except PermissionError:
        with suppress(Exception):
            await websocket.close(code=4403)
    except (ValueError, TypeError, asyncio.TimeoutError):
        with suppress(Exception):
            await websocket.close(code=4401)
    except WebSocketDisconnect:
        pass
    except Exception:
        with suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if connection:
            hub.disconnect(connection)
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
