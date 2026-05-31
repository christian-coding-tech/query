import json
import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import paho.mqtt.client as mqtt

from queue_system import load_queue, add_to_queue, serve_next, save_queue

DATA_DIR = os.path.dirname(__file__)
HISTORY_FILE = os.path.join(DATA_DIR, "queue_history.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

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


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {LOGIN_USERNAME: generate_password_hash(LOGIN_PASSWORD)}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def validate_user(username, password):
    users = load_users()
    hashed = users.get(username)
    if not hashed:
        return False
    return check_password_hash(hashed, password)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


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


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if session.get("user"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not username or not password:
            flash("Enter a username and password.", "warning")
        elif password != confirm_password:
            flash("Passwords do not match.", "warning")
        else:
            users = load_users()
            if username in users:
                flash("This username already exists.", "warning")
            else:
                users[username] = generate_password_hash(password)
                save_users(users)
                flash("Account created. Please login.", "success")
                return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user"):
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if validate_user(username, password):
            session["user"] = username
            flash("You are now logged in.", "success")
            return redirect(url_for("index"))
        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    queue = load_queue()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Please enter a name to add to the queue.", "warning")
        else:
            add_to_queue(queue, name)
            publish_queue_update(queue)
            flash(f"Added {name} to the queue.", "success")
            return redirect(url_for("index"))

    return render_template("index.html", queue=queue)


@app.route("/serve", methods=["POST"])
@login_required
def serve():
    queue = load_queue()
    item = serve_next(queue)
    if item:
        log_served(item)
        flash(f"Serving {item['name']} (id={item['id']}).", "success")
    else:
        flash("There are no items in the queue.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    queue = load_queue()
    history = load_history()
    return render_template(
        "dashboard.html",
        queue=queue,
        history=history,
        served_count=len(history),
        next_item=queue[0] if queue else None,
        newest=queue[-1] if queue else None,
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
@login_required
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
    app.run(debug=True)
