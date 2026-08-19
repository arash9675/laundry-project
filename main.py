import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode
from pywebpush import WebPushException, webpush

BASE_DIR = Path(__file__).resolve().parent
VAPID_KEYS_FILE = BASE_DIR / 'vapid_keys.json'
SUBSCRIPTIONS_FILE = BASE_DIR / 'push_subscriptions.json'
NOTIFIED_FILE = BASE_DIR / 'push_notified.json'
VAPID_SUBJECT = 'mailto:laundry@example.com'


def load_vapid():
    if VAPID_KEYS_FILE.exists():
        data = json.loads(VAPID_KEYS_FILE.read_text())
        return Vapid.from_pem(data['private_pem'].encode('utf8'))
    vapid = Vapid()
    vapid.generate_keys()
    VAPID_KEYS_FILE.write_text(json.dumps({
        'private_pem': vapid.private_pem().decode('utf8'),
        'public_pem': vapid.public_pem().decode('utf8')
    }))
    return vapid


VAPID = load_vapid()


def public_key_base64url(vapid):
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(raw)


def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def save_json(path, data):
    path.write_text(json.dumps(data))


# machine_id -> list of push subscription dicts
push_subscriptions = load_json(SUBSCRIPTIONS_FILE, {})
# "machine_id|expected_end_time" -> True (already notified for this cycle)
push_notified = load_json(NOTIFIED_FILE, {})
# in-memory set of countdown buckets already sent: "machine_id|end_iso|bucket"
countdown_sent = set()


async def send_push(subscription, machine_id, title, body, tag='laundry-finished',
                    renotify=True, require_interaction=True, silent=False):
    payload = json.dumps({
        'title': title,
        'body': body,
        'machineId': machine_id,
        'url': f'./index.html?machine={machine_id}',
        'tag': tag,
        'renotify': renotify,
        'requireInteraction': require_interaction,
        'silent': silent
    })
    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=VAPID,
            vapid_claims={'sub': VAPID_SUBJECT}
        )
        return True
    except WebPushException as e:
        # 404/410 mean the subscription is no longer valid -> drop it
        if e.response is not None and e.response.status_code in (404, 410):
            print(f"Push subscription gone for {machine_id}: {e.response.status_code}")
            return False
        print("Push error:", e)
        return True
    except Exception as e:
        # Malformed subscription (bad keys, etc.) -> drop it instead of crashing the scheduler.
        print(f"Push error (dropping subscription for {machine_id}):", e)
        return False


async def send_to_machine(machine_id, title, body, tag, renotify=True,
                          require_interaction=True, silent=False):
    """Send a push to every subscription of a machine, pruning dead ones."""
    subscriptions = push_subscriptions.get(machine_id) or []
    kept = [s for s in subscriptions
            if await send_push(s, machine_id, title, body, tag, renotify, require_interaction, silent)]
    if kept:
        push_subscriptions[machine_id] = kept
    else:
        push_subscriptions.pop(machine_id, None)
    save_json(SUBSCRIPTIONS_FILE, push_subscriptions)
    return kept


async def send_initial_countdown(machine_id):
    """Immediately show a lock-screen 'alarm set' notification with live remaining."""
    try:
        records = sheet.get_all_records()
        for row in records:
            if str(row.get('Machine_ID', '')).strip() != machine_id:
                continue
            status = str(row.get('Status', '')).strip()
            end_raw = str(row.get('Expected_End_Time', '')).strip()
            body = f'Machine {machine_id}: alarm set'
            if status == 'In Use' and end_raw:
                try:
                    end = datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
                    now = datetime.now(timezone.utc)
                    if end > now:
                        rem = int((end - now).total_seconds() // 60)
                        body = f'Machine {machine_id}: {rem} min remaining'
                except ValueError:
                    pass
            await send_to_machine(machine_id, 'Laundry timer', body,
                                  f'laundry-countdown-{machine_id}',
                                  renotify=True, require_interaction=False, silent=True)
            break
    except Exception as e:
        print('Initial countdown error:', e)


async def check_and_send_pushes():
    global countdown_sent, five_min_sent
    try:
        records = sheet.get_all_records()
    except Exception as e:
        print("Scheduler sheet read error:", e)
        return

    now = datetime.now(timezone.utc)
    for i, record in enumerate(records):
        machine_id = str(record.get('Machine_ID', '')).strip()
        status = str(record.get('Status', '')).strip()
        end_raw = str(record.get('Expected_End_Time', '')).strip()
        if status != 'In Use' or not machine_id or not end_raw:
            continue
        try:
            end_time = datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
        except ValueError:
            continue

        if end_time <= now:
            # Cycle finished -> loud, persistent notification (once per cycle)
            notify_key = f"{machine_id}|{end_time.isoformat()}"
            if notify_key not in push_notified:
                await send_to_machine(machine_id, 'Laundry Finished!',
                                      f'Machine {machine_id} is ready.',
                                      'laundry-finished', renotify=True,
                                      require_interaction=True, silent=False)
                push_notified[notify_key] = True
                save_json(NOTIFIED_FILE, push_notified)
                # Drop stale countdown buckets for this machine
                countdown_sent = {k for k in countdown_sent if not k.startswith(machine_id + '|')}
                five_min_sent = {k for k in five_min_sent if not k.startswith(machine_id + '|')}

            # Auto-release: mark the machine available so the sheet reflects reality.
            try:
                sheet.update(f"C{i+2}:G{i+2}", [[
                    "Available", "", "", "", datetime.now(timezone.utc).isoformat()
                ]])
                push_subscriptions.pop(machine_id, None)
                reported_error_machines.pop(machine_id, None)
                save_json(SUBSCRIPTIONS_FILE, push_subscriptions)
                save_json(REPORTED_ERRORS_FILE, reported_error_machines)
            except Exception as e:
                print("Auto-release error:", e)
        else:
            # Still running -> refresh the lock-screen countdown every ~5 minutes
            remaining = int((end_time - now).total_seconds() // 60)

            # One-time persistent "about 5 minutes left" warning.
            if 0 < remaining <= 5:
                five_key = f"{machine_id}|{end_time.isoformat()}|5min"
                if five_key not in five_min_sent:
                    await send_to_machine(machine_id, 'Almost done',
                                          f'Machine {machine_id}: about 5 minutes left.',
                                          f'laundry-five-min-{machine_id}',
                                          renotify=True, require_interaction=True, silent=False)
                    five_min_sent.add(five_key)

            bucket = max(0, remaining // 5)
            bucket_key = f"{machine_id}|{end_time.isoformat()}|{bucket}"
            if bucket_key not in countdown_sent:
                await send_to_machine(machine_id, 'Laundry timer',
                                      f'Machine {machine_id}: {remaining} min remaining',
                                      f'laundry-countdown-{machine_id}',
                                      renotify=True, require_interaction=False, silent=True)
                countdown_sent.add(bucket_key)


async def scheduler_loop():
    while True:
        try:
            await check_and_send_pushes()
        except Exception as e:
            print("Scheduler error:", e)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
REPORTED_ERRORS_FILE = BASE_DIR / 'reported_errors.json'
reported_error_machines = load_json(REPORTED_ERRORS_FILE, {})

# Fair-use rate limiting (server-side; never based on client-supplied timestamps).
MAX_ACTIVE_MACHINES_PER_USER = 3
MACHINE_DEBOUNCE = timedelta(minutes=1)
REPORT_WINDOW = timedelta(minutes=10)
REPORT_MAX_PER_WINDOW = 2
report_history = {}  # client_id -> list of ISO timestamps

five_min_sent = set()   # "machine_id|end_iso|5min" -> already sent the 5-min warning


def get_client_id(request: Request):
    cid = request.headers.get('X-Client-Id', '').strip()
    if cid:
        return cid
    host = request.client.host if request.client else 'unknown'
    return f"ip:{host}"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to Google Sheets
scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
client = gspread.authorize(creds)
sheet = client.open("Laundry_System_DB").worksheet("Machines")

@app.get("/get_machines")
async def get_machines():
    records = sheet.get_all_records()
    now = datetime.now(timezone.utc)
    for record in records:
        machine_id = str(record.get('Machine_ID', ''))
        reported = machine_id in reported_error_machines
        # When the machine's cycle time is done, clear any reported problem
        end_raw = str(record.get('Expected_End_Time', '')).strip()
        if reported and end_raw:
            try:
                end = datetime.fromisoformat(end_raw.replace('Z', '+00:00'))
                if end <= now:
                    reported_error_machines.pop(machine_id, None)
                    save_json(REPORTED_ERRORS_FILE, reported_error_machines)
                    reported = False
            except ValueError:
                pass
        record['Reported_Error'] = reported
        record['Reported_Error_At'] = reported_error_machines.get(machine_id)
    return records


@app.post("/report_machine_problem")
async def report_machine_problem(request: Request):
    data = await request.json()
    machine_id = str(data.get('machine_id', '')).strip()
    client_id = str(data.get('client_id', '')).strip() or get_client_id(request)
    now = datetime.now(timezone.utc)

    if not machine_id:
        return JSONResponse(
            status_code=400,
            content={"status": "invalid_request", "error": "Machine ID is required."}
        )

    # Limit how many problem reports a single user can send in a window,
    # so a neighbour can't flood the board with false alarms.
    recent = []
    for ts in report_history.get(client_id, []):
        try:
            t = datetime.fromisoformat(ts)
            if now - t < REPORT_WINDOW:
                recent.append(ts)
        except ValueError:
            pass
    if len(recent) >= REPORT_MAX_PER_WINDOW:
        return JSONResponse(
            status_code=429,
            content={"status": "too_many_reports", "error": "Too many reports. Please try again later."}
        )
    report_history[client_id] = recent + [now.isoformat()]

    all_data = sheet.get_all_records()
    for row in all_data:
        if str(row.get('Machine_ID', '')) == machine_id:
            reported_error_machines[machine_id] = now.isoformat()
            save_json(REPORTED_ERRORS_FILE, reported_error_machines)
            return {
                "status": "success",
                "message": f"Machine {machine_id} problem reported."
            }

    return JSONResponse(
        status_code=404,
        content={"status": "not_found", "error": "Machine not found."}
    )


@app.get("/vapid_public_key")
async def vapid_public_key():
    return {"public_key": public_key_base64url(VAPID)}


@app.post("/subscribe")
async def subscribe(request: Request):
    data = await request.json()
    machine_id = str(data.get('machine_id', '')).strip()
    subscription = data.get('subscription') or {}
    if not machine_id or not subscription.get('endpoint'):
        return JSONResponse(
            status_code=400,
            content={"status": "invalid_request", "error": "Machine ID and subscription are required."}
        )
    subscriptions = push_subscriptions.setdefault(machine_id, [])
    subscriptions[:] = [s for s in subscriptions if s.get('endpoint') != subscription['endpoint']]
    subscriptions.append(subscription)
    save_json(SUBSCRIPTIONS_FILE, push_subscriptions)
    # Fire the first lock-screen countdown immediately (don't wait for the 30s tick)
    asyncio.create_task(send_initial_countdown(machine_id))
    return {"status": "success", "message": f"Subscribed for machine {machine_id}."}


@app.post("/unsubscribe")
async def unsubscribe(request: Request):
    data = await request.json()
    machine_id = str(data.get('machine_id', '')).strip()
    endpoint = (data.get('subscription') or {}).get('endpoint') or data.get('endpoint')
    subscriptions = push_subscriptions.get(machine_id, [])
    if endpoint:
        subscriptions[:] = [s for s in subscriptions if s.get('endpoint') != endpoint]
    else:
        push_subscriptions.pop(machine_id, None)
    save_json(SUBSCRIPTIONS_FILE, push_subscriptions)
    return {"status": "success", "message": "Unsubscribed."}


@app.post("/update_machine")
async def update_machine(request: Request):
    data = await request.json()
    machine_id = str(data.get('machine_id')).strip()
    new_status = data.get('status')  # "In Use" or "Available"
    user_id = str(data.get('user_id', '')).strip() or get_client_id(request)
    now = datetime.now(timezone.utc)

    all_data = sheet.get_all_records()

    # A user may run several machines at once (e.g. a washer and a dryer),
    # but not an unlimited number. Count their currently active machines.
    if new_status == "In Use":
        active = 0
        for row in all_data:
            if str(row.get('Machine_ID', '')).strip() == machine_id:
                continue
            if str(row.get('Status', '')).strip() == "In Use" and str(row.get('User_ID', '')).strip() == user_id:
                active += 1
        if active >= MAX_ACTIVE_MACHINES_PER_USER:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "limit_reached",
                    "error": f"You can run at most {MAX_ACTIVE_MACHINES_PER_USER} machines at once."
                }
            )

    for i, row in enumerate(all_data):
        if str(row['Machine_ID']).strip() != machine_id:
            continue

        last_upd_raw = str(row.get('Last_Updated', '')).strip()
        if last_upd_raw:
            try:
                last_upd = datetime.fromisoformat(last_upd_raw.replace("Z", "+00:00"))
            except ValueError:
                last_upd = datetime.min.replace(tzinfo=timezone.utc)
        else:
            last_upd = datetime.min.replace(tzinfo=timezone.utc)

        # Server-side debounce: block rapid re-registration of the same machine.
        if (now - last_upd) < MACHINE_DEBOUNCE:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "too_soon",
                    "error": "Too many requests. Please wait a moment."
                }
            )

        sheet.update(f"C{i+2}:G{i+2}", [[
            new_status,
            data.get('program', ''),
            data.get('expected_end_time', ''),
            user_id,
            now.isoformat()
        ]])

        if new_status == "Available":
            push_subscriptions.pop(machine_id, None)
            reported_error_machines.pop(machine_id, None)
            save_json(SUBSCRIPTIONS_FILE, push_subscriptions)
            save_json(REPORTED_ERRORS_FILE, reported_error_machines)

        return {
            "status": "success",
            "message": f"Machine {machine_id} is now {new_status}."
        }

    return JSONResponse(
        status_code=404,
        content={"status": "not_found", "error": "Machine not found."}
    )

app.mount("/", StaticFiles(directory="static", html=True), name="static")
