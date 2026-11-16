# Live updates

The application uses one authenticated WebSocket per browser tab at /api/v1/live. REST APIs still load and save data. Socket messages contain only invalidation topics and authorized centre IDs, never amounts, references, patient details or tokens.

## Connected pages

- Payout workspace, payout setup, billing reports and therapist My Earnings
- Payment Alerts (updates wait while a rule has unsaved edits)
- Notification bell (the previous one-minute polling has been removed)
- Appointment calendar (refreshes wait while a booking/payment/edit dialog is open)

Changes are collected from supported SQLAlchemy ORM, bulk update/delete, and simple SQL INSERT/UPDATE/DELETE operations and published after the outer database commit. Rollbacks do not publish. Changes made directly by a database administrator outside this application are not observed. Additional pages can subscribe with useLiveUpdates in src/contexts/RealtimeContext.tsx; extend the topic map in app/services/realtime_events.py for additional tables.

## Running

Restart the backend after deploying this change, then reload the frontend. Uvicorn's standard installation already includes WebSocket support. No Redis server, Redis Python package, or additional database tables are required.

Run exactly one backend worker, in development and production. The Gunicorn configuration sets workers = 1. All browser WebSockets share the same in-process event hub. Do not override this with --workers greater than 1 or run multiple backend instances: events are not relayed across processes. Remove old REDIS_URL, REALTIME_REQUIRE_REDIS, and REALTIME_CHANNEL entries from deployment environment files; they are no longer used.

Example: gunicorn app.main:app -c gunicorn_conf.py

Alternatively: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

For an HTTPS frontend, configure VITE_API_BASE_URL with an HTTPS backend URL so the socket uses wss. Include the frontend's exact Origin in backend CORS_ORIGINS. Tokens are supplied in the first socket frame rather than in URLs or proxy logs. Users are checked against the database at connect and every minute, and sockets close when tokens expire or permissions are revoked. Region subscriptions are intersected with the user's current assigned regions. Notification messages are restricted to their recipient.

## Reverse proxy

Preserve the /api/v1/live path. For Nginx, add the following inside the backend server block and use the deployment's existing upstream address:

    location /api/v1/live {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

## Recovery and request control

Reconnect uses exponential backoff with jitter, capped around 30 seconds. A successful reconnect requests one debounced resync of mounted views. Hidden tabs retain pending changes and refresh once visible. Open forms queue changes until editing finishes. Changes from the same tab are suppressed because its save handler already updates the UI. Bursts are debounced for 350 milliseconds; unrelated topics/centres do not fetch. Slow clients reconnect rather than silently losing messages. No page reload, interval-based payout recalculation or notification polling is used.

## Tests

Frontend: npm run test:requests (includes reconnect, queued-edit, hidden-tab and cache tests).
Backend: python -m unittest discover -s tests -p test_realtime.py
Also run the payout calculation regression tests and frontend typecheck/build.
