"""Smoke test for the MARZ backend. Run with: python check.py"""
# flake8: noqa: E501
import io
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "check.db")
os.environ["OPENROUTER_API_KEY"] = "test-key"

import app as marz  # noqa: E402
import live  # noqa: E402

FAILED = []
HEAD = {"X-User-ID": "check-user-0123456789"}
IMAGE = "data:image/jpeg;base64,AAAA"
FACT = "User studies GCSE Computer Science"


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        FAILED.append(name)


SEEN = {}


def fake_model(messages):
    SEEN["system"] = messages[0]["content"]
    SEEN["user"] = messages[-1]["content"]
    return "Hello! [[MEMORY: " + FACT + "]]"


marz.call_openrouter = fake_model
client = marz.app.test_client()


def post_chat(**body):
    return client.post("/api/chat", json=body, headers=HEAD)


def upload(size, mime="image/png"):
    picture = (io.BytesIO(b"x" * size), "a.png", mime)
    return client.post(
        "/api/chat",
        data={"message": "what is this", "image": picture},
        headers=HEAD,
        content_type="multipart/form-data",
    )


def get_json(path):
    return client.get(path, headers=HEAD).json


health = client.get("/api/health")
check("health endpoint", health.status_code == 200 and health.json["ok"])
check("missing user id rejected", client.get("/api/conversations").status_code == 401)

first = post_chat(message="I am studying GCSE Computer Science")
cid = first.json.get("conversation_id")
check("chat returns a clean reply", first.status_code == 200 and "[[" not in first.json["reply"])
check("memory marker stored", first.json["memories"] == [FACT])
check("conversation listed", len(get_json("/api/conversations")) == 1)
check("history has 2 messages", len(get_json("/api/conversations/%d" % cid)) == 2)
check("long-term memory saved", [m["content"] for m in get_json("/api/r/memories")] == [FACT])

SAMPLES = (
    ("notes", {"title": "t", "body": "b"}),
    ("projects", {"name": "p", "description": "d"}),
    ("saved", {"content": "c"}),
)
for res, body in SAMPLES:
    client.post("/api/r/" + res, json=body, headers=HEAD)
    rows = get_json("/api/r/" + res)
    check(res + " create and list", len(rows) == 1)
    done = client.delete("/api/r/%s/%d" % (res, rows[0]["id"]), headers=HEAD)
    check(res + " delete", done.status_code == 200 and get_json("/api/r/" + res) == [])

saved = client.put("/api/profile", json={"name": "Sam", "bio": "Student"}, headers=HEAD)
check("profile saved", saved.json["name"] == "Sam")
prefs = client.put("/api/settings", json={"theme": "dark"}, headers=HEAD)
check("settings saved", prefs.json["theme"] == "dark")

check("JSON image accepted", post_chat(message="hi", image=IMAGE).status_code == 200)
check("bad image rejected", post_chat(message="hi", image="data:text/html;base64,AAAA").status_code == 400)
check("file upload accepted", upload(10).status_code == 200)
check("oversized upload rejected", upload(4 * 1024 * 1024 + 1).status_code == 400)
check("wrong upload type rejected", upload(10, "text/plain").status_code == 400)

client.delete("/api/conversations/%d" % cid, headers=HEAD)
check("conversation deleted", client.get("/api/conversations/%d" % cid, headers=HEAD).status_code == 404)
check("unknown resource 404", client.get("/api/r/nope", headers=HEAD).status_code == 404)

# ---------- live time and weather ----------
FORECAST = {
    "current_units": {
        "temperature_2m": "C",
        "wind_speed_10m": "km/h",
        "precipitation": "mm",
    },
    "current": {
        "time": "2026-10-19T14:15",
        "temperature_2m": 12.5,
        "apparent_temperature": 10.1,
        "relative_humidity_2m": 80,
        "precipitation": 0.2,
        "weather_code": 61,
        "wind_speed_10m": 14.0,
    },
    "daily": {
        "weather_code": [61, 3],
        "temperature_2m_max": [14, 13],
        "temperature_2m_min": [8, 7],
        "precipitation_probability_max": [70, 20],
    },
}
SPOTS = {
    "tokyo": {"name": "Tokyo, Japan", "lat": 35.7, "lon": 139.7,
              "tz": "Asia/Tokyo"},
    "london": {"name": "London, England", "lat": 51.5, "lon": -0.1,
               "tz": "Europe/London"},
}


def fake_geocode(name):
    return SPOTS.get(name.lower())


def fake_forecast(spot, imperial):
    return FORECAST


def failing_forecast(spot, imperial):
    raise live.requests.RequestException("offline")


def has(lines, text):
    return any(text in line for line in lines)


live.geocode = fake_geocode
live.fetch_forecast = fake_forecast
live._cache.clear()

lines = live.live_context("hello there", tz="Europe/London")
check("local time always included", len(lines) == 1 and "Europe/London" in lines[0])
lines = live.live_context("What time is it in Tokyo?", tz="Europe/London")
check("time in another city", has(lines, "Local date and time in Tokyo"))
lines = live.live_context("What's the weather in London right now?")
check("weather for a named city", has(lines, "Current weather in London") and has(lines, "light rain"))
check("tomorrow forecast included", has(live.live_context("weather in London"), "Tomorrow: overcast"))
check("weather without a location asks", has(live.live_context("how is the weather?"), "location is unknown"))
check("weather from browser coordinates", has(live.live_context("weather?", coords=(51.5, -0.1)), "the user's location"))
check("weather from saved home city", has(live.live_context("weather today", city="London"), "Current weather in London"))
check("place finder", live.find_place("weather in New York right now?") == "New York" and live.find_place("is it raining in the garden") is None)
live.fetch_forecast = failing_forecast
lines = live.live_context("weather?", coords=(10.0, 20.0))
check("weather failure handled", has(lines, "could not be fetched"))
live.fetch_forecast = fake_forecast

city = client.put("/api/profile", json={"city": "London"}, headers=HEAD)
check("home city saved", city.json["city"] == "London")
post_chat(message="how's the weather?", timezone="Europe/London", locale="en-GB")
check("chat prompt carries live weather", "Live data" in SEEN["system"] and "Current weather in London" in SEEN["system"])
post_chat(message="hello", timezone="Asia/Tokyo")
check("chat prompt carries local time", "Asia/Tokyo" in SEEN["system"])

# ---------- tools, timers, notes, files ----------
import io as _io  # noqa: E402
import zipfile  # noqa: E402

import tools  # noqa: E402


def run(name, **args):
    body = {"name": name, "args": args, "timezone": "Europe/London"}
    reply = client.post("/api/tool", json=body, headers=HEAD)
    return reply.json["result"], reply.json["state"]


check("duration parsing", [tools.parse_duration(x) for x in ("30 minutes", "1 hour 30 minutes", "half an hour", "90 seconds", "5-minute timer", "gibberish")] == [1800, 5400, 1800, 90, 300, None])
result, state = run("setTimer", duration="5 minutes", label="tea")
check("setTimer", result["ok"] and len(state["timers"]) == 1 and 290 < state["timers"][0]["remaining"] <= 300)
check("bad duration refused", not run("setTimer", duration="soon")[0]["ok"])
check("listTimers", "tea" in run("listTimers")[0]["text"])
result, state = run("cancelTimer", timer_id="tea")
check("cancelTimer by label", result["ok"] and state["timers"] == [])
check("cancel with none active", not run("cancelTimer", timerId="all")[0]["ok"])
check("stopwatch needs a start", not run("pauseStopwatch")[0]["ok"])
states = [run(n)[1]["stopwatch"] for n in ("startStopwatch", "pauseStopwatch", "resumeStopwatch", "stopStopwatch", "resetStopwatch")]
check("stopwatch lifecycle", [s["status"] for s in states] == ["active", "paused", "active", "stopped", "idle"] and states[0]["running"] and not states[1]["running"] and states[4]["elapsed"] == 0)
check("getStopwatchTime", run("getStopwatchTime")[0]["ok"])
check("getLocalTime", run("getLocalTime")[0]["data"]["timezone"] == "Europe/London")
check("getLocalTime for a city", run("getLocalTime", location="Tokyo")[0]["data"]["timezone"] == "Asia/Tokyo")
check("getCurrentDate", "2026" in run("getCurrentDate")[0]["text"] or run("getCurrentDate")[0]["ok"])
weather = run("getWeather", location="London")[0]
check("getWeather data for the widget", weather["data"]["place"].startswith("London") and weather["data"]["humidity"] == 80)
check("createNote and searchNotes", run("createNote", title="Shopping", content="milk and eggs")[0]["ok"] and "milk" in run("searchNotes", query="eggs")[0]["text"])
check("saveMemory and searchMemory", run("saveMemory", content="Likes jazz")[0]["ok"] and "jazz" in run("searchMemory", query="jazz")[0]["text"])
check("search treats percent literally", "No notes" in run("searchNotes", query="100%")[0]["text"])
check("openWebsite", run("openWebsite", url="example.com")[0]["open"].startswith("https://example.com"))
check("openWebsite rejects bad schemes", not run("openWebsite", url="javascript:alert(1)")[0]["ok"])
check("unknown tool", not run("hackTheMoon")[0]["ok"])
check("tool history kept", len(client.get("/api/tools", headers=HEAD).json["history"]) >= 10)
check("quick intents", [tools.quick_calls(t)[0][0] for t in ("Set a timer for 5 minutes", "What time is it?", "Start a stopwatch", "show my timers", "cancel the timer", "What's the weather in London?")] == ["setTimer", "getLocalTime", "startStopwatch", "listTimers", "cancelTimer", "getWeather"])
check("no false timer trigger", tools.quick_calls("My timer app crashes after 5 minutes") == [])

marz.call_openrouter = fake_model
chat = post_chat(message="Set a timer for 2 minutes", timezone="Europe/London")
check("chat runs a timer tool", chat.json["tools"][0]["name"] == "setTimer" and len(chat.json["state"]["timers"]) == 1 and "setTimer" in SEEN["system"])
chat = post_chat(message="What time is it?", timezone="Europe/London")
check("chat time tool result reaches the model", "Local time in your area" in SEEN["system"])

replies = iter(['[[TOOL: {"name": "createNote", "args": {"title": "Idea", "content": "robots"}}]]', "Saved your note."])


def scripted_model(messages):
    return next(replies)


marz.call_openrouter = scripted_model
chat = post_chat(message="Jot down an idea about robots")
check("model-chosen tool call", chat.json["reply"] == "Saved your note." and chat.json["tools"][0]["name"] == "createNote")
check("note saved by the model", "robots" in run("searchNotes", query="robots")[0]["text"])
marz.call_openrouter = fake_model

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 20
DOCX = _io.BytesIO()
with zipfile.ZipFile(DOCX, "w") as archive:
    archive.writestr("word/document.xml", "\x3cw:p\x3e\x3cw:t\x3eHello docx\x3c/w:t\x3e\x3c/w:p\x3e")


def send_files(*files, message="look"):
    data = {"message": message, "files": [(_io.BytesIO(b), n, "application/octet-stream") for n, b in files]}
    return client.post("/api/chat", data=data, headers=HEAD, content_type="multipart/form-data")


reply = send_files(("notes.txt", b"Plain text file"), ("shot1.png", PNG), ("shot2.png", PNG), ("report.docx", DOCX.getvalue()))
parts = SEEN["user"]
check("multiple files and images in one message", reply.status_code == 200 and len(parts) == 3 and "--- file: notes.txt ---" in parts[0]["text"] and "Hello docx" in parts[0]["text"])
check("image only message", send_files(("a.png", PNG), message="").status_code == 200 and "Describe" in SEEN["user"][0]["text"])
reply = send_files(("virus.exe", b"MZ\x90\x00" + b"x" * 20), ("fake.pdf", b"%PDF-1.4 broken"))
check("unsafe and broken files are reported", reply.status_code == 200 and len(reply.json["warnings"]) == 2)
reply = send_files(("n.txt", b"hi"), message="")
check("file only message", reply.status_code == 200 and "Summarise" in SEEN["user"])

if FAILED:
    print("\n%d check(s) failed." % len(FAILED))
    sys.exit(1)
print("\nALL CHECKS PASSED")
