import json
import os
from datetime import datetime

DATA_FILE = os.path.join(os.path.dirname(__file__), "queue_data.json")


def load_queue():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_queue(queue):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)


def add_to_queue(queue, name, note=None, priority="Normal"):
    next_id = max((item.get("id", 0) for item in queue), default=0) + 1
    item = {
        "id": next_id,
        "name": name,
        "note": note or "",
        "priority": priority if priority in ("Normal", "High") else "Normal",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    queue.append(item)
    save_queue(queue)
    return item


def serve_next(queue):
    if not queue:
        return None
    item = queue.pop(0)
    save_queue(queue)
    return item


def remove_from_queue(queue, ticket_id):
    item = next((it for it in queue if it.get("id") == ticket_id), None)
    if not item:
        return None
    queue.remove(item)
    save_queue(queue)
    return item


def list_queue(queue):
    if not queue:
        print("The queue is currently empty.")
        return
    print("Current queue:")
    for idx, item in enumerate(queue, start=1):
        print(f"{idx}. {item['name']} (id={item['id']}, added={item['created_at']})")


def clear_queue():
    save_queue([])
    print("Queue cleared.")


def print_help():
    print("Queue system commands:")
    print("  python queue_system.py add <name>    - Add a person/item to the queue")
    print("  python queue_system.py serve         - Serve the next item in line")
    print("  python queue_system.py list          - Show current queue")
    print("  python queue_system.py clear         - Remove all items from queue")
    print("  python queue_system.py help          - Show this help message")


def main():
    queue = load_queue()
    import sys

    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1].lower()

    if command == "add":
        if len(sys.argv) < 3:
            print("Usage: python queue_system.py add <name>")
            return
        name = " ".join(sys.argv[2:])
        item = add_to_queue(queue, name)
        print(f"Added to queue: {item['name']} (id={item['id']})")
    elif command == "serve":
        item = serve_next(queue)
        if item:
            print(f"Serving: {item['name']} (id={item['id']})")
        else:
            print("No items in the queue to serve.")
    elif command == "list":
        list_queue(queue)
    elif command == "clear":
        clear_queue()
    elif command == "help":
        print_help()
    else:
        print(f"Unknown command: {command}")
        print_help()


if __name__ == "__main__":
    main()
