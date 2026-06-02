import json
import os
from datetime import datetime
import logging
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response, stream_with_context
import threading
import queue as std_queue
from werkzeug.security import generate_password_hash, check_password_hash
import paho.mqtt.client as mqtt

from queue_system import load_queue, add_to_queue, serve_next, save_queue

DATA_DIR = os.path.dirname(__file__)
HISTORY_FILE = os.path.join(DATA_DIR, "queue_history.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
REQUESTS_FILE = os.path.join(DATA_DIR, "queue_requests.json")

LOGIN_USERNAME = os.environ.get("QUEUE_USER", "admin")
LOGIN_PASSWORD = os.environ.get("QUEUE_PASS", "admin")

MQTT_BROKER_URL = os.environ.get("MQTT_BROKER_URL")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD")
MQTT_USE_TLS = os.environ.get("MQTT_USE_TLS", "false").lower() in ("1", "true", "yes")
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "queue/updates")
MQTT_WS_URL = os.environ.get("MQTT_WS_URL")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("QUEUE_SYSTEM_SECRET", "please-change-this-secret")

# Subscribers for Server-Sent Events (SSE)
SUBSCRIBERS = []
SUBSCRIBERS_LOCK = threading.Lock()
logging.basicConfig(level=logging.INFO)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def log_served(item):
    history = load_history()
    record = {
        "id": item["id"],
        "name": item["name"],
        "created_at": item["created_at"],
        "served_at": datetime.now().isoformat(timespec="seconds"),
    }
    history.append(record)
    save_history(history)
    publish_queue_update(load_queue())
    return history


def publish_mqtt(topic, payload):
    if not MQTT_BROKER_URL:
        return
    try:
        client = mqtt.Client()
        if MQTT_USERNAME:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        if MQTT_USE_TLS:
            client.tls_set()
        client.connect(MQTT_BROKER_URL, MQTT_BROKER_PORT, 60)
        client.publish(topic, payload)
        client.disconnect()
    except Exception:
        pass


def publish_queue_update(queue):
    payload = json.dumps(
        {
            "now_serving": queue[0] if queue else None,
            "queue": queue,
            "count": len(queue),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
    )
    publish_mqtt(MQTT_TOPIC, payload)
    # Broadcast to SSE subscribers (non-blocking)
    with SUBSCRIBERS_LOCK:
        for q in list(SUBSCRIBERS):
            try:
                q.put_nowait(payload)
            except Exception:
                logging.exception("Failed to push payload to SSE subscriber")
                continue
    return payload


@app.route('/stream')
def stream():
    def gen(client_q):
        try:
            while True:
                try:
                    data = client_q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except std_queue.Empty:
                    # keep-alive comment to prevent proxies from closing
                    yield ": keep-alive\n\n"
        finally:
            # cleanup handled outside
            return

    client_q = std_queue.Queue()
    with SUBSCRIBERS_LOCK:
        SUBSCRIBERS.append(client_q)
        logging.info("SSE subscriber added (total=%d)", len(SUBSCRIBERS))

    @stream_with_context
    def stream_generator():
        try:
            yield from gen(client_q)
        finally:
            with SUBSCRIBERS_LOCK:
                try:
                    SUBSCRIBERS.remove(client_q)
                except ValueError:
                    pass

    resp = Response(stream_generator(), mimetype='text/event-stream')
    # Recommended headers for SSE
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/favicon.ico')
def favicon():
    # Return an empty response for favicon requests to avoid 404 noise.
    return "", 204


@app.route('/_test_push')
def _test_push():
    # Debug helper: push a test payload to subscribers when running in debug mode
    if not app.debug:
        return jsonify({"error": "Not available"}), 404
    sample = [{"id": 1, "name": f"Test {datetime.now().isoformat(timespec='seconds')}", "created_at": datetime.now().isoformat(timespec='seconds')}]
    publish_queue_update(sample)
    return jsonify({"status": "ok", "sent": sample})


def normalize_users(users):
    normalized = {}
    for username, record in users.items():
        if isinstance(record, str):
            normalized[username] = {"password": record, "role": "staff"}
        elif isinstance(record, dict):
            normalized[username] = {
                "password": record.get("password", ""),
                "role": record.get("role", "staff"),
            }
        else:
            continue
    return normalized


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return normalize_users(json.load(f))
    return {LOGIN_USERNAME: {"password": generate_password_hash(LOGIN_PASSWORD), "role": "staff"}}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def validate_user(username, password):
    users = load_users()
    user = users.get(username)
    if user and check_password_hash(user.get("password", ""), password):
        return True
    if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
        return True
    return False


def get_user_role(username):
    users = load_users()
    user = users.get(username)
    if user:
        return user.get("role", "staff")
    if username == LOGIN_USERNAME:
        return "staff"
    return None


def load_requests():
    if os.path.exists(REQUESTS_FILE):
        with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_requests(requests):
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(requests, f, indent=2)


def find_ticket(queue, ticket_id):
    return next((item for item in queue if item.get("id") == ticket_id), None)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def staff_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if session.get("role") not in ("staff", "admin"):
            flash("Staff access only.", "warning")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped_view


@app.context_processor
def inject_mqtt_config():
    return {
        "mqtt_ws_url": MQTT_WS_URL,
        "mqtt_topic": MQTT_TOPIC,
    }


@app.context_processor
def inject_mqtt_config():
    return {
        "mqtt_ws_url": MQTT_WS_URL,
        "mqtt_topic": MQTT_TOPIC,
    }


def fallback_ai_suggestion(message, context):
    queue = context.get("queue") or []
    now_next = queue[0] if queue else None
    count = len(queue)

    if not message:
        message = ""

    # Deterministic, staff-friendly fallback suggestions.
    lower = message.lower()

    if any(k in lower for k in ("announce", "call", "next")):
        if now_next:
            return (
                f"Suggested call: Please proceed, {now_next['name']} (ticket #{now_next['id']}). "
                "Have a seat if you’re not there yet."
            )
        return "Suggested call: The queue is currently empty. Please wait for the next ticket."

    if any(k in lower for k in ("polite", "greeting", "thank")):
        if now_next:
            return (
                f"Polite greeting idea: Thank you for waiting. {now_next['name']}, you’re next. "
                "If you need anything, please let us know."
            )
        return "Polite greeting idea: Thank you for your patience. We’re ready—please wait for the next update."

    if count >= 10:
        return "Operational tip: The queue is long—announce briefly, and consider calling in small batches to reduce confusion."

    return "Tip: Keep it short—call the next ticket, confirm the name, and repeat only if needed."


def build_ai_prompt(message, context):
    queue = context.get("queue") or []
    now_next = queue[0] if queue else None

    queue_preview = [
        {"id": it.get("id"), "name": it.get("name"), "created_at": it.get("created_at")}
        for it in queue[:10]
    ]

    next_str = (
        f"Next ticket: #{now_next.get('id')} - {now_next.get('name')}"
        if now_next
        else "No tickets in queue"
    )

    return (
        "You are an assistant helping a staff member run a service queue. "
        "Give a short, practical suggestion tailored to the current queue. "
        "Keep it under 80 words.\n\n"
        f"Staff request: {message}\n\n"
        f"{next_str}.\n"
        f"Queue (first up to 10): {json.dumps(queue_preview, ensure_ascii=False)}\n\n"
        "Return only the suggestion text."
    )


def fallback_ai_chat(messages, context):
    queue = context.get("queue") or []
    now_next = queue[0] if queue else None
    latest = messages[-1]["content"] if messages else ""
    lower = latest.lower()

    if any(k in lower for k in ("how", "help", "use", "guide", "work", "system", "explain")):
        return (
            "This queue system helps staff manage ticket flow. "
            "Use the main Queue page to add a new customer, call the next ticket, and clear the line. "
            "The Dashboard shows queue size, served totals, next ticket, and recent history. "
            "The Display page is for public viewing of the current ticket. "
            "If you need help with login or accounts, use the Login or Signup page."
        )

    if any(k in lower for k in ("add", "ticket", "queue", "entry")):
        return (
            "To add someone, enter their name on the main Queue page and submit. "
            "The system creates a ticket with an auto ID and timestamp, then shows it in the waiting list."
        )

    if any(k in lower for k in ("serve", "next", "call", "waiting")):
        return (
            "Press Call next on the Dashboard or Queue page to serve the next person. "
            "That removes the ticket from the waiting queue and stores it in served history."
        )

    if any(k in lower for k in ("clear", "reset", "empty")):
        return (
            "Use the Clear queue button to empty the current waiting list. "
            "This keeps served history intact while resetting the active queue."
        )

    return (
        "I can help explain how the queue, dashboard, display, and login features work. "
        "Ask me anything about using the system and I’ll guide you step by step."
    )


def build_ai_chat_system_prompt(context):
    queue = context.get("queue") or []
    now_next = queue[0] if queue else None
    queue_summary = (
        f"Current queue size: {len(queue)}. "
        f"Next ticket: #{now_next.get('id')} - {now_next.get('name')}.")
    if not now_next:
        queue_summary = f"Current queue size: {len(queue)}. No tickets are waiting right now."

    return (
        "You are a helpful assistant that guides staff through using a simple service queue web app. "
        "Answer questions clearly and politely, focusing on how the system works, where to click, "
        "and what each page does. Do not ask for external or private data. "
        f"{queue_summary}"
    )


@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    payload = request.get_json(silent=True) or {}
    messages = payload.get("messages", [])
    context = payload.get("context", {})

    if not OPENAI_API_KEY:
        reply = fallback_ai_chat(messages, context)
        return jsonify({"mode": "fallback", "reply": reply})

    try:
        from openai import OpenAI

        system_prompt = build_ai_chat_system_prompt(context)
        client = OpenAI(api_key=OPENAI_API_KEY)

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system_prompt}] + messages,
            temperature=0.4,
            max_tokens=250,
        )

        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("Empty AI response")
        return jsonify({"mode": "openai", "reply": text})
    except Exception as e:
        reply = fallback_ai_chat(messages, context)
        return jsonify({"mode": "fallback", "reply": reply, "error": str(e)}), 200


@app.route("/signup")
def signup():
    flash("Account creation is restricted. Ask a staff member to create a viewer account.", "info")
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if validate_user(username, password):
            session["user"] = username
            session["role"] = get_user_role(username) or "staff"
            flash("You are now logged in.", "success")
            return redirect(url_for("index"))
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    queue = load_queue()
    user_requests = []
    if session.get("role") == "viewer":
        requests = load_requests()
        user_requests = [r for r in requests if r.get("requested_by") == session.get("user")]
    return render_template("index.html", queue=queue, requests=user_requests)


@app.route("/add", methods=["POST"])
@login_required
@staff_required
def add_ticket():
    queue = load_queue()
    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip()
    priority = request.form.get("priority", "Normal")
    if not name:
        flash("Please enter a ticket number to add to the queue.", "warning")
    elif any(item.get("name") == name for item in queue):
        flash(f"Ticket number {name} already exists in the queue.", "danger")
    else:
        add_to_queue(queue, name, note=note, priority=priority)
        publish_queue_update(queue)
        flash(f"Added ticket #{name} to the queue.", "success")
    return redirect(url_for("index"))


@app.route("/request", methods=["POST"])
@login_required
def request_queue():
    if session.get("role") != "viewer":
        flash("Only viewers can request new queue entries.", "warning")
        return redirect(url_for("index"))

    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip()
    if not name:
        flash("Please enter a ticket number to request.", "warning")
        return redirect(url_for("index"))

    if not name.isdigit() or not (0 <= int(name) <= 9999):
        flash("Please enter a valid ticket number between 0 and 9999.", "warning")
        return redirect(url_for("index"))

    queue = load_queue()
    if any(item.get("name") == name for item in queue):
        flash(f"Ticket number {name} is already in the queue.", "danger")
        return redirect(url_for("index"))

    requests = load_requests()
    if any(r.get("name") == name and r.get("status") == "pending" for r in requests):
        flash(f"Ticket number {name} has already been requested and is pending.", "warning")
        return redirect(url_for("index"))

    request_id = max((r.get("id", 0) for r in requests), default=0) + 1
    requests.append(
        {
            "id": request_id,
            "name": name,
            "note": note,
            "requested_by": session.get("user"),
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "status": "pending",
        }
    )
    save_requests(requests)
    flash("Your queue request has been sent to staff.", "success")
    return redirect(url_for("index"))


@app.route("/requests")
@login_required
def requests_page():
    requests = load_requests()
    if session.get("role") == "viewer":
        requests = [r for r in requests if r.get("requested_by") == session.get("user")]
    return render_template("requests.html", requests=requests)


@app.route("/serve", methods=["POST"])
@login_required
@staff_required
def serve():
    queue = load_queue()
    item = serve_next(queue)
    if item:
        log_served(item)
        flash(f"Serving {item['name']} (id={item['id']}).", "success")
    else:
        flash("There are no items in the queue.", "info")
    return redirect(url_for("index"))


@app.route("/ticket/<int:ticket_id>/serve", methods=["POST"])
@login_required
@staff_required
def serve_ticket(ticket_id):
    queue = load_queue()
    item = find_ticket(queue, ticket_id)
    if item:
        queue.remove(item)
        save_queue(queue)
        log_served(item)
        flash(f"Served ticket #{ticket_id}: {item['name']}", "success")
        publish_queue_update(queue)
    else:
        flash("Ticket not found.", "warning")
    return redirect(url_for("index"))


@app.route("/ticket/<int:ticket_id>/remove", methods=["POST"])
@login_required
@staff_required
def remove_ticket(ticket_id):
    queue = load_queue()
    item = find_ticket(queue, ticket_id)
    if item:
        queue.remove(item)
        save_queue(queue)
        publish_queue_update(queue)
        flash(f"Removed ticket #{ticket_id}: {item['name']}", "info")
    else:
        flash("Ticket not found.", "warning")
    return redirect(url_for("index"))


@app.route("/viewer/create", methods=["GET", "POST"])
@login_required
@staff_required
def create_viewer():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("Username and password are required.", "warning")
            return redirect(url_for("create_viewer"))

        users = load_users()
        if username in users:
            flash("This username already exists.", "warning")
            return redirect(url_for("create_viewer"))

        users[username] = {"password": generate_password_hash(password), "role": "viewer"}
        save_users(users)
        flash("Viewer account created.", "success")
        return redirect(url_for("manage_viewers"))

    return render_template("create_viewer.html")


@app.route("/viewer/manage", methods=["GET"])
@login_required
@staff_required
def manage_viewers():
    users = load_users()
    viewers = {username: user for username, user in users.items() if user.get("role") == "viewer"}
    return render_template("manage_viewers.html", viewers=viewers)


@app.route("/viewer/<username>/edit", methods=["GET", "POST"])
@login_required
@staff_required
def edit_viewer(username):
    users = load_users()
    user = users.get(username)
    
    if not user or user.get("role") != "viewer":
        flash("Viewer not found.", "warning")
        return redirect(url_for("manage_viewers"))
    
    if request.method == "POST":
        new_password = request.form.get("password", "").strip()
        if not new_password:
            flash("Please enter a new password.", "warning")
            return redirect(url_for("edit_viewer", username=username))
        
        user["password"] = generate_password_hash(new_password)
        users[username] = user
        save_users(users)
        flash(f"Password for {username} has been updated.", "success")
        return redirect(url_for("manage_viewers"))
    
    return render_template("edit_viewer.html", username=username, user=user)


@app.route("/viewer/<username>/delete", methods=["POST"])
@login_required
@staff_required
def delete_viewer(username):
    users = load_users()
    user = users.get(username)
    
    if not user or user.get("role") != "viewer":
        flash("Viewer not found.", "warning")
        return redirect(url_for("manage_viewers"))
    
    del users[username]
    save_users(users)
    flash(f"Viewer account {username} has been deleted.", "success")
    return redirect(url_for("manage_viewers"))


@app.route("/request/<int:request_id>/accept", methods=["POST"])
@login_required
@staff_required
def accept_request(request_id):
    requests = load_requests()
    queued = load_queue()
    request_item = next((r for r in requests if r.get("id") == request_id), None)
    if not request_item:
        flash("Request not found.", "warning")
        return redirect(url_for("dashboard"))

    if request_item.get("status") != "pending":
        flash("Request has already been handled.", "info")
        return redirect(url_for("dashboard"))

    add_to_queue(queued, request_item.get("name"), note=request_item.get("note", ""), priority="Normal")
    save_queue(queued)
    publish_queue_update(queued)
    request_item["status"] = "accepted"
    save_requests(requests)
    flash(f"Accepted request #{request_id} and added it to the queue.", "success")
    return redirect(url_for("dashboard"))


@app.route("/request/<int:request_id>/reject", methods=["POST"])
@login_required
@staff_required
def reject_request(request_id):
    requests = load_requests()
    request_item = next((r for r in requests if r.get("id") == request_id), None)
    if not request_item:
        flash("Request not found.", "warning")
        return redirect(url_for("dashboard"))

    if request_item.get("status") != "pending":
        flash("Request has already been handled.", "info")
        return redirect(url_for("dashboard"))

    request_item["status"] = "rejected"
    save_requests(requests)
    flash(f"Rejected request #{request_id}.", "info")
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    queue = load_queue()
    history = load_history()
    wait_minutes = []
    for item in history:
        try:
            created = datetime.fromisoformat(item["created_at"])
            served = datetime.fromisoformat(item["served_at"])
            wait_minutes.append((served - created).total_seconds() / 60)
        except Exception:
            continue

    average_wait = f"{round(sum(wait_minutes) / len(wait_minutes), 1)} min" if wait_minutes else "N/A"
    high_priority = sum(1 for item in queue if item.get("priority") == "High")
    requests = load_requests()
    pending_requests = [r for r in requests if r.get("status") == "pending"]

    return render_template(
        "dashboard.html",
        queue=queue,
        history=history,
        served_count=len(history),
        next_item=queue[0] if queue else None,
        newest=queue[-1] if queue else None,
        average_wait=average_wait,
        high_priority=high_priority,
        pending_requests=pending_requests,
    )


@app.route("/display")
@login_required
def display():
    queue = load_queue()
    return render_template(
        "display.html",
        queue=queue,
        now_serving=queue[0] if queue else None,
        next_in_line=queue[1] if len(queue) > 1 else None,
        waiting_count=len(queue),
        mqtt_ws_url=MQTT_WS_URL,
        mqtt_topic=MQTT_TOPIC,
    )


@app.route("/public")
def public_display():
    queue = load_queue()
    return render_template(
        "public_display.html",
        queue=queue,
        now_serving=queue[0] if queue else None,
        next_in_line=queue[1] if len(queue) > 1 else None,
        waiting_count=len(queue),
        mqtt_ws_url=MQTT_WS_URL,
        mqtt_topic=MQTT_TOPIC,
    )


@app.route("/clear", methods=["POST"])
@login_required
def clear():
    save_queue([])
    publish_queue_update([])
    flash("Queue has been cleared.", "info")
    return redirect(url_for("index"))


@app.route("/ai/suggest", methods=["POST"])
def ai_suggest():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    context = payload.get("context") or {}

    # Always available fallback.
    if not OPENAI_API_KEY:
        suggestion = fallback_ai_suggestion(message, context)
        return jsonify({"mode": "fallback", "suggestion": suggestion})

    try:
        from openai import OpenAI  # Optional dependency (only needed when OPENAI_API_KEY is set)

        prompt = build_ai_prompt(message, context)
        client = OpenAI(api_key=OPENAI_API_KEY)

        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You write short helpful suggestions for service queue staff.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=120,
        )

        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("Empty AI response")
        return jsonify({"mode": "openai", "suggestion": text})
    except Exception as e:
        suggestion = fallback_ai_suggestion(message, context)
        return jsonify({"mode": "fallback", "suggestion": suggestion, "error": str(e)}), 200


if __name__ == "__main__":
    # Use threaded server to support SSE connections alongside normal requests
    app.run(debug=True, host="0.0.0.0", threaded=True)
