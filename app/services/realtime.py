"""Small, permission-scoped invalidations; no patient or financial data on sockets."""
import asyncio
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class LivePrincipal:
    user_id: int
    region_ids: frozenset
    topics: frozenset
    expires_at: float
    all_regions: bool = False


def visible_event(principal, event):
    targets = set(event.get('user_ids', []))
    if targets and principal.user_id not in targets:
        return None
    topics = set(event.get('topics', [])) & principal.topics
    regions = set(event.get('region_ids', []))
    if not topics or (not targets and not regions and not event.get('global')):
        return None
    if regions and not principal.all_regions:
        regions &= principal.region_ids
        if not regions:
            return None
    return {'type': 'invalidate', 'id': event['id'], 'topics': sorted(topics),
            'region_ids': sorted(regions), 'at': event.get('at')}


@dataclass(eq=False)
class LiveConnection:
    principal: LivePrincipal
    client_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=64))


class RealtimeHub:
    def __init__(self):
        self.connections = set()
        self.loop = None
        self.tasks = set()
        self.node_id = uuid.uuid4().hex

    async def start(self):
        """Deliver live events inside the single application worker; no external broker."""
        self.loop = asyncio.get_running_loop()
        self.node_id = uuid.uuid4().hex

    async def stop(self):
        self.loop = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for connection in list(self.connections):
            self._enqueue(connection, {'type': 'close'})
        self.connections.clear()

    def connect(self, principal, client_id):
        connection = LiveConnection(principal, client_id)
        self.connections.add(connection)
        return connection

    def disconnect(self, connection):
        self.connections.discard(connection)

    def _enqueue(self, connection, message):
        if connection.queue.full():
            # Reconnect/resync instead of silently losing an invalidation.
            while not connection.queue.empty():
                connection.queue.get_nowait()
            connection.queue.put_nowait({'type': 'close'})
        else:
            connection.queue.put_nowait(message)

    def _deliver(self, event):
        for connection in list(self.connections):
            if connection.principal.expires_at <= time.time():
                self._enqueue(connection, {'type': 'unauthorized'})
                continue
            if 'access' not in event.get('topics', []) and event.get('origin') == connection.client_id and event.get('actor_id') == connection.principal.user_id:
                continue
            message = visible_event(connection.principal, event)
            if message:
                self._enqueue(connection, message)

    async def publish(self, event):
        event = {'id': uuid.uuid4().hex, 'at': time.time(), **event, 'node': self.node_id}
        self._deliver(event)

    def publish_threadsafe(self, event):
        loop = self.loop
        if not loop or loop.is_closed():
            return
        def schedule():
            task = loop.create_task(self.publish(event))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        loop.call_soon_threadsafe(schedule)


hub = RealtimeHub()
