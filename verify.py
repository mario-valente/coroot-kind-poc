import json
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import urlopen


def get(url, expected_status=200):
    try:
        response = urlopen(url, timeout=30)
    except HTTPError as error:
        if error.code != expected_status:
            raise
        response = error
    with response:
        assert response.status == expected_status, response.status
        return json.load(response)


def eventually(check, description, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            print(f"OK: {description}")
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timeout: {description}")
        time.sleep(5)


base = "http://127.0.0.1:8080"
user = get(f"{base}/api/user")
project = next(p["id"] for p in user["projects"] if p["name"] == "default")
api = f"{base}/api/project/{project}"
app_id = f"{project}:demo:Deployment:app-a"

normal = get("http://127.0.0.1:8081/checkout")
assert normal["flow"] == ["app-a", "app-b", "redis"]
assert normal["trace_id"] == normal["reservation"]["trace_id"]
assert normal["reservation"]["count"] > 0
print("OK: app-a -> app-b -> Redis; mesmo trace_id nas duas apps")

start = time.monotonic()
get("http://127.0.0.1:8081/checkout?scenario=slow")
assert time.monotonic() - start >= 0.6
error = get("http://127.0.0.1:8081/checkout?scenario=error", 502)
get("http://127.0.0.1:8081/checkout?scenario=invalid", 400)
print("OK: cenarios normal, lento, erro simulado e parametro invalido")


def spans_for(trace_id):
    query = urlencode({"trace": f"otel:{trace_id}"})
    result = get(f"{api}/app/{quote(app_id, safe='')}/tracing?{query}")
    return (result.get("data") or {}).get("spans") or []


def check_trace():
    spans = spans_for(normal["trace_id"])
    servers = {s["name"]: s for s in spans if s["name"].startswith("GET /")}
    db = [s for s in spans if s["attributes"].get("db.system") == "redis"]
    clients = [s for s in spans if "app-b:8080" in s["attributes"].get("http.url", "")]
    if not ({"GET /checkout", "GET /reserve"} <= servers.keys() and db and clients):
        return False
    assert clients[0]["parent_id"] == servers["GET /checkout"]["id"]
    assert servers["GET /reserve"]["parent_id"] == clients[0]["id"]
    assert all(s["parent_id"] == servers["GET /reserve"]["id"] for s in db)
    assert {"INCRBY", "SET", "GET"} <= {s["name"] for s in db}
    return True


eventually(check_trace, "trace armazenado no Coroot com cadeia pai/filho e comandos Redis")
eventually(
    lambda: any(s["status"]["error"] for s in spans_for(error["trace_id"])),
    "erro simulado visivel no trace",
)


def check_map():
    result = get(f"{api}/overview/map")
    nodes = (result.get("data") or {}).get("map") or []
    edges = {
        (node["id"].split(":")[-1], upstream["id"].split(":")[-1])
        for node in nodes
        if ":demo:" in node["id"]
        for upstream in node["upstreams"]
    }
    return {
        ("load-generator", "app-a"),
        ("app-a", "app-b"),
        ("app-b", "redis"),
    } <= edges


eventually(check_map, "Service Map: load-generator -> app-a -> app-b -> redis")
print(f"Mapa: {base}/p/{project}/map")
print(
    f"Trace: {base}/p/{project}/app/{quote(app_id, safe='')}/Tracing?"
    + urlencode({"trace": f"otel:{normal['trace_id']}"})
)
