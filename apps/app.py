import json
import logging
import os
import threading
import time

import redis
import requests
from flask import Flask, jsonify, request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ROLE = os.environ["APP_ROLE"]
if ROLE not in ("app-a", "app-b"):
    raise ValueError("APP_ROLE must be app-a or app-b")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(ROLE)
provider = TracerProvider(
    resource=Resource.create(
        {
            "service.name": ROLE,
            "service.namespace": "demo",
            "k8s.namespace.name": "demo",
            "k8s.pod.name": os.environ.get("HOSTNAME", ROLE),
        }
    )
)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
RequestsInstrumentor().instrument()
RedisInstrumentor().instrument()
app = Flask(__name__)
FlaskInstrumentor().instrument_app(app, excluded_urls="healthz")
http_clients = threading.local()
cache = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    socket_connect_timeout=2,
    socket_timeout=2,
    decode_responses=True,
)


def trace_id():
    return f"{trace.get_current_span().get_span_context().trace_id:032x}"


@app.get("/healthz")
def health():
    return jsonify(status="ok", service=ROLE)


@app.get("/checkout")
def checkout():
    if ROLE != "app-a":
        return jsonify(error="Use app-a for checkout"), 404
    scenario = request.args.get("scenario", "normal")
    if scenario not in ("normal", "slow", "error"):
        return jsonify(error="scenario must be normal, slow or error"), 400
    trace.get_current_span().set_attribute("business.feature", "checkout")
    trace.get_current_span().set_attribute("demo.scenario", scenario)
    try:
        if not hasattr(http_clients, "session"):
            http_clients.session = requests.Session()
        response = http_clients.session.get(
            f"{os.environ.get('APP_B_URL', 'http://app-b:8080')}/reserve",
            params={"scenario": scenario},
            timeout=5,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("checkout dependency failed trace_id=%s", trace_id())
        return jsonify(error="app-b request failed", trace_id=trace_id()), 502
    logger.info(
        json.dumps(
            {"feature": "checkout", "scenario": scenario, "trace_id": trace_id()}
        )
    )
    return jsonify(
        flow=["app-a", "app-b", "redis"],
        feature="checkout",
        trace_id=trace_id(),
        reservation=result,
    )


@app.get("/reserve")
def reserve():
    if ROLE != "app-b":
        return jsonify(error="Use app-b for reserve"), 404
    scenario = request.args.get("scenario", "normal")
    if scenario not in ("normal", "slow", "error"):
        return jsonify(error="scenario must be normal, slow or error"), 400
    trace.get_current_span().set_attribute("business.feature", "checkout")
    trace.get_current_span().set_attribute("demo.scenario", scenario)
    if scenario == "slow":
        time.sleep(0.6)
    if scenario == "error":
        logger.error("simulated reservation failure trace_id=%s", trace_id())
        return jsonify(error="simulated reservation failure", trace_id=trace_id()), 503
    try:
        count = cache.incr("poc:checkout:reservations")
        cache.set("poc:checkout:last-trace", trace_id(), ex=3600)
        stored_trace = cache.get("poc:checkout:last-trace")
    except redis.RedisError:
        logger.exception("redis request failed trace_id=%s", trace_id())
        return jsonify(error="redis request failed", trace_id=trace_id()), 503
    logger.info(
        json.dumps(
            {"feature": "checkout", "reservation": count, "trace_id": trace_id()}
        )
    )
    return jsonify(count=count, trace_id=trace_id(), stored_trace=stored_trace)
