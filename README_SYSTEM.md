# Queue System (Python + Flask)

This project provides:
1) A simple **command-line queue manager** (`queue_system.py`) that stores data in `queue_data.json`.
2) A **web app** (`queue_web.py`) to add/serve/clear queue items and view history.
3) Optional **MQTT real-time updates** for the public display screen (`/display` / `/public`).

---

## Architecture overview

### Data files
- **`queue_data.json`**: current queue items (added/served)
- **`queue_history.json`**: served history (who was served and when)
- **`users.json`**: staff user accounts (hashed passwords)

### Core logic
- `queue_system.py`
  - `add_to_queue(queue, name)`: appends an item with an auto `id` and timestamp
  - `serve_next(queue)`: removes and returns the first item in the queue
  - `load_queue()` / `save_queue(queue)`: read/write `queue_data.json`

- `queue_web.py` (Flask)
  - Uses the functions above to manage queue state.
  - Records served items into `queue_history.json`.
  - Publishes queue updates via MQTT (if configured).

### Web pages / routes
- `/login` (POST/GET): staff login
- `/signup` (POST/GET): create a staff account
- `/` : main queue page (add + see queue)
- `/serve` : serve next item (POST)
- `/dashboard` : admin dashboard (metrics + history)
- `/display` : public queue display (optionally live via MQTT)
- `/public` : public queue display (optionally live via MQTT)
- `/clear` : clear queue (POST)

---

## Requirements

### Python dependencies
See `requirements.txt`:
- Flask
- paho-mqtt

---

## How to run

### 1) Command-line queue
Run from the project folder:

```bash
python queue_system.py add Alice
python queue_system.py add Bob
python queue_system.py list
python queue_system.py serve
python queue_system.py clear
```

### 2) Web app
1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start the server:

```bash
python queue_web.py
```

3. Open the app:
- Main app: http://127.0.0.1:5000/
- Login: http://127.0.0.1:5000/login
- Dashboard: http://127.0.0.1:5000/dashboard
- Public display: http://127.0.0.1:5000/display

---

## Login / accounts

### Default credentials (if `users.json` does not exist yet)
- username: `admin`
- password: `admin`

### Customizing via environment variables
You can override the default staff login:
- `QUEUE_USER`
- `QUEUE_PASS`

Example (Windows CMD):

```bat
set QUEUE_USER=admin
set QUEUE_PASS=admin
python queue_web.py
```

### Creating accounts
Go to:
- http://127.0.0.1:5000/signup

---

## MQTT real-time updates (optional)

The public display page (`/display` / `/public`) can update live when the queue changes.

### Server-side MQTT publishing
`queue_web.py` publishes to `MQTT_TOPIC` whenever the queue changes (serve/add/clear).

Set these environment variables before starting `queue_web.py`:
- `MQTT_BROKER_URL` **(required to enable MQTT publishing)**
- `MQTT_BROKER_PORT` (default: `1883`)
- `MQTT_USERNAME` / `MQTT_PASSWORD` (optional)
- `MQTT_USE_TLS` (`true` / `false`, default: `false`)
- `MQTT_TOPIC` (default: `queue/updates`)

### Browser-side subscription (WebSocket MQTT)
The display page includes a browser MQTT client only when `MQTT_WS_URL` is set.

Set:
- `MQTT_WS_URL` (required for the browser to subscribe via WebSockets)

**Note:** `MQTT_WS_URL` is separate from `MQTT_BROKER_URL`. It must be a WebSocket-enabled endpoint compatible with the browser MQTT client.

---

## Message format (MQTT payload)

When the server publishes, the payload is JSON:

```json
{
  "now_serving": { "id": 1, "name": "Alice", "created_at": "..." } | null,
  "queue": [ ... ],
  "count": 3,
  "timestamp": "..."
}
```

The display page reads `payload.queue` and renders:
- now serving: `queue[0]`
- next: `queue[1]`
- waiting count: `queue.length`

---

## Where the queue data is stored

All queue state persists in JSON files in the same directory:
- `queue_data.json`
- `queue_history.json`
- `users.json`

---

## Troubleshooting

- **Web app won’t start**: ensure `Flask` is installed (`pip install -r requirements.txt`).
- **Login fails**: check `users.json` exists and credentials match; or create a user at `/signup`.
- **MQTT display not updating**:
  - confirm `MQTT_BROKER_URL` is set (server publishing)
  - confirm `MQTT_WS_URL` is set (browser subscription)
  - confirm both use the same `MQTT_TOPIC`

---

## Project files (quick map)
- `queue_system.py` : CLI queue operations
- `queue_web.py` : Flask web app
- `queue_data.json` / `queue_history.json` / `users.json` : persistent storage
- `templates/` : HTML pages
- `static/` : CSS

