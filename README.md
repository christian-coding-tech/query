# Python Queue System

A simple command-line queue manager built in Python.

## Usage

Run commands from the `queue_system` folder.

```bash
python queue_system.py add Alice
python queue_system.py add Bob
python queue_system.py list
python queue_system.py serve
python queue_system.py clear
```

## Web Interface

Install dependencies and run the web app from the same folder:

```bash
pip install -r requirements.txt
python queue_web.py
```

Open http://127.0.0.1:5000 in your browser to manage the queue from the web.

Use http://127.0.0.1:5000/signup to create a new staff account if you don't have credentials yet.

Then visit http://127.0.0.1:5000/dashboard to see the admin dashboard with queue metrics and served history.

Or visit http://127.0.0.1:5000/display for a public queue display screen (great for showing on a TV or monitor).

### Login

The admin interface requires login at `/login`.
Default credentials are:
- username: `admin`
- password: `admin`

You can override these with environment variables:
- `QUEUE_USER`
- `QUEUE_PASS`

### MQTT real-time updates

If you want real-time display updates, set the MQTT broker environment variables before running the app:
- `MQTT_BROKER_URL` (required for MQTT publish)
- `MQTT_BROKER_PORT` (default: `1883`)
- `MQTT_USERNAME` and `MQTT_PASSWORD` (optional)
- `MQTT_USE_TLS=true` (optional)
- `MQTT_TOPIC` (default: `queue/updates`)
- `MQTT_WS_URL` (required for the browser display page to subscribe over WebSockets)

If MQTT is configured, the public display page will update automatically when the queue changes.

## Features

- Add items to the queue
- Serve the next item in line
- List current queue state
- Clear the entire queue
- Automatically stores queue data in `queue_data.json`
