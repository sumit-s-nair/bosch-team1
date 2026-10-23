"""Recorded incident scenarios for offline development and evaluation.

Built from the OpenTelemetry demo's source code: the error messages, log lines and span shapes are the ones
each failure path really emits (payment/charge.js, cart ValkeyCartStore.cs, product-catalog main.go,
recommendation RecommendationService.java, ad AdService.java, checkout main.go, envoy access-log format),
covering every incident type on the hackathon brief.

TEST DATA ONLY. `expected` labels are used by eval/ to score the agent; agent/ code must never import this.
"""
import copy

# ---- healthy baseline (numbers taken from our live `scripts.eyes` run) --------------------------------
SERVICES = {  # service: (rps, p95_ms)
    "frontend-proxy": (14.0, 38), "frontend": (12.3, 38), "product-catalog": (5.1, 40), "image-provider": (3.7, 2),
    "cart": (1.45, 4), "currency": (1.0, 2), "recommendation": (0.8, 7), "ad": (0.42, 4),
    "shipping": (0.25, 4), "quote": (0.18, 2), "checkout": (0.07, 48), "payment": (0.07, 2), "email": (0.07, 5),
}
INFRA = ["valkey-cart", "astronomy-db", "otel-collector", "prometheus", "jaeger", "opensearch", "grafana",
         "load-generator", "flagd", "flagd-ui", "frontend-proxy"]
CONTAINERS = sorted(set(SERVICES) | set(INFRA))
CPU = {"load-generator": 31, "payment": 6, "otel-collector": 3, "opensearch": 3, "frontend": 2}
MEM = {"opensearch": 84, "ad": 81, "astronomy-db": 81, "flagd-ui": 74, "flagd": 71}

OPS = {  # service: [(kind, span_name, share of service rps)]
    "frontend": [("SERVER", "GET /api/products/[productId]/index", .1), ("SERVER", "GET /api/cart", .12),
                 ("SERVER", "POST /api/checkout", .01), ("SERVER", "GET /api/recommendations", .12),
                 ("CLIENT", "oteldemo.ProductCatalogService/GetProduct", .3), ("CLIENT", "oteldemo.CartService/GetCart", .12),
                 ("CLIENT", "oteldemo.CheckoutService/PlaceOrder", .006), ("CLIENT", "oteldemo.RecommendationService/ListRecommendations", .06),
                 ("CLIENT", "oteldemo.AdService/GetAds", .03)],
    "checkout": [("SERVER", "oteldemo.CheckoutService/PlaceOrder", 1), ("CLIENT", "oteldemo.PaymentService/Charge", 1),
                 ("CLIENT", "oteldemo.CartService/EmptyCart", 1), ("CLIENT", "oteldemo.CartService/GetCart", 1),
                 ("CLIENT", "oteldemo.EmailService/SendOrderConfirmation", 1), ("CLIENT", "oteldemo.ShippingService/ShipOrder", 1)],
    "payment": [("SERVER", "oteldemo.PaymentService/Charge", 1), ("INTERNAL", "charge", 1)],
    "cart": [("SERVER", "oteldemo.CartService/GetCart", .6), ("SERVER", "oteldemo.CartService/AddItem", .3),
             ("SERVER", "oteldemo.CartService/EmptyCart", .05), ("CLIENT", "HGET", .6), ("CLIENT", "HMSET", .3)],
    "product-catalog": [("SERVER", "oteldemo.ProductCatalogService/GetProduct", .8),
                        ("SERVER", "oteldemo.ProductCatalogService/ListProducts", .2), ("CLIENT", "SELECT catalog.products", 1)],
    "recommendation": [("SERVER", "oteldemo.RecommendationService/ListRecommendations", 1), ("INTERNAL", "get_product_list", 1),
                       ("CLIENT", "oteldemo.ProductCatalogService/ListProducts", 1)],
    "ad": [("SERVER", "oteldemo.AdService/GetAds", 1)],
    "currency": [("SERVER", "Currency/Convert", 1)], "shipping": [("SERVER", "POST /get-quote", 1), ("CLIENT", "POST /getquote", 1)],
    "quote": [("SERVER", "POST /getquote", 1)], "email": [("SERVER", "POST /send_order_confirmation", 1)],
    "image-provider": [("SERVER", "GET /static", 1)], "frontend-proxy": [("SERVER", "ingress", 1)],
}

CHAINS = {  # request paths used to synthesise traces: (service, span name)
    "checkout_payment": [("frontend", "POST /api/checkout"), ("frontend", "oteldemo.CheckoutService/PlaceOrder"),
                         ("checkout", "oteldemo.CheckoutService/PlaceOrder"), ("checkout", "oteldemo.PaymentService/Charge"),
                         ("payment", "oteldemo.PaymentService/Charge"), ("payment", "charge")],
    "checkout_emptycart": [("frontend", "POST /api/checkout"), ("checkout", "oteldemo.CheckoutService/PlaceOrder"),
                           ("checkout", "oteldemo.CartService/EmptyCart"), ("cart", "oteldemo.CartService/EmptyCart"),
                           ("cart", "HMSET")],
    "get_cart": [("frontend", "GET /api/cart"), ("frontend", "oteldemo.CartService/GetCart"),
                 ("cart", "oteldemo.CartService/GetCart"), ("cart", "HGET")],
    "get_product": [("frontend", "GET /api/products/[productId]/index"), ("frontend", "oteldemo.ProductCatalogService/GetProduct"),
                    ("product-catalog", "oteldemo.ProductCatalogService/GetProduct"), ("product-catalog", "SELECT catalog.products")],
    "recommendations": [("frontend", "GET /api/recommendations"), ("frontend", "oteldemo.RecommendationService/ListRecommendations"),
                        ("recommendation", "oteldemo.RecommendationService/ListRecommendations"), ("recommendation", "get_product_list"),
                        ("recommendation", "oteldemo.ProductCatalogService/ListProducts"),
                        ("product-catalog", "oteldemo.ProductCatalogService/ListProducts")],
    "ads": [("frontend", "GET /api/data"), ("frontend", "oteldemo.AdService/GetAds"), ("ad", "oteldemo.AdService/GetAds")],
    "home": [("frontend-proxy", "ingress"), ("frontend", "GET /"), ("frontend", "oteldemo.ProductCatalogService/ListProducts"),
             ("product-catalog", "oteldemo.ProductCatalogService/ListProducts")],
}


def base():
    return {
        "name": "healthy", "description": "No fault: normal load.",
        "expected": {"decision": ["no_incident", "escalate"], "services": [], "categories": ["unknown"]},
        "services": {s: {"rps": r, "err": 0.0, "p95": p, "base_rps": r, "base_err": 0.0, "base_p95": p}
                     for s, (r, p) in SERVICES.items()},
        "ops": {},          # (service, span_name) -> {"err": x, "p95": y, "rps_mult": z}
        "containers": {c: {"state": "running", "status": "Up 2 hours", "restart_count": 0, "exit_code": 0,
                           "oom_killed": False, "started_min_ago": 120} for c in CONTAINERS},
        "cpu": dict(CPU), "mem": dict(MEM), "mem_growth": {}, "mem_ttl": {},
        "redis": {"up": True, "clients": 3, "rejected": 0},
        # traces: (chain, origin_index or None, origin_message, slow_index, slow_ms, attrs)
        #   origin_index: deepest failing span (-1 = last); it and its ancestors are errors, its children are absent
        "traces": [],
        "logs": [],         # (service, severity_text, severity_number, body, count)
    }


def _healthy_traffic(sc):
    for chain in CHAINS:
        sc["traces"] += [(chain, None, "", None, 0, {})] * 3


def svc(sc, name, **kw):
    sc["services"][name].update(kw)


def op(sc, service, span, **kw):
    sc["ops"].setdefault((service, span), {}).update(kw)


def cont(sc, name, **kw):
    sc["containers"][name].update(kw)


def scenario(name, description, expected):
    sc = base()
    sc.update(name=name, description=description, expected=expected)
    _healthy_traffic(sc)
    return sc


# ---- app faults ---------------------------------------------------------------------------------------
def payment_failure():
    sc = scenario("payment_failure", "Payment service fails 50% of charges (app fault).",
                  {"decision": ["propose_fix", "escalate"], "services": ["payment"], "categories": ["service_error"]})
    msg = "Payment request failed. Invalid token. demo.user_context.loyalty_level=gold"
    svc(sc, "payment", err=.5); svc(sc, "checkout", err=.5); svc(sc, "frontend", err=.004)
    op(sc, "payment", "oteldemo.PaymentService/Charge", err=.5); op(sc, "payment", "charge", err=.5)
    op(sc, "checkout", "oteldemo.CheckoutService/PlaceOrder", err=.5); op(sc, "checkout", "oteldemo.PaymentService/Charge", err=.5)
    op(sc, "frontend", "POST /api/checkout", err=.5); op(sc, "frontend", "oteldemo.CheckoutService/PlaceOrder", err=.5)
    sc["traces"] += [("checkout_payment", -1, msg, None, 0, {})] * 4
    sc["logs"] += [("checkout", "ERROR", 17, f"failed to charge card: could not charge the card: rpc error: code = Unknown desc = {msg}", 6),
                   ("frontend", "error", 17, f"Error: 2 UNKNOWN: failed to charge card: could not charge the card: rpc error: code = Unknown desc = {msg}", 6)]
    return sc


def cart_intermittent():
    sc = scenario("cart_intermittent", "Cart service fails EmptyCart intermittently (bad cart store connection).",
                  {"decision": ["propose_fix", "escalate"], "services": ["cart"], "categories": ["service_error", "dependency_failure", "cache_unavailable"]})
    msg = ("Can't access cart storage. StackExchange.Redis.RedisConnectionException: It was not possible to connect to "
           "the redis server(s). UnableToConnect on badhost:1234/Interactive")
    svc(sc, "cart", err=.03); svc(sc, "checkout", err=.5)
    op(sc, "cart", "oteldemo.CartService/EmptyCart", err=.5); op(sc, "checkout", "oteldemo.CartService/EmptyCart", err=.5)
    op(sc, "checkout", "oteldemo.CheckoutService/PlaceOrder", err=.5)
    sc["traces"] += [("checkout_emptycart", 3, msg, None, 0, {})] * 3     # origin: cart EmptyCart server span
    sc["logs"] += [("checkout", "ERROR", 17, f"failed to empty user cart during checkout: rpc error: code = FailedPrecondition desc = {msg}", 3)]
    return sc


def product_catalog_one_product():
    sc = scenario("product_catalog_one_product", "Product catalog fails GetProduct for one specific product id.",
                  {"decision": ["propose_fix", "escalate"], "services": ["product-catalog"], "categories": ["service_error"]})
    msg = "Error: Product Catalog Fail Feature Flag Enabled"   # real message; flagguard must redact it
    svc(sc, "product-catalog", err=.1); svc(sc, "frontend", err=.03)
    op(sc, "product-catalog", "oteldemo.ProductCatalogService/GetProduct", err=.12)
    op(sc, "frontend", "oteldemo.ProductCatalogService/GetProduct", err=.12)
    op(sc, "frontend", "GET /api/products/[productId]/index", err=.12)
    sc["traces"] += [("get_product", 2, msg, None, 0, {"demo.product.id": "OLJCESPC7Z"})] * 3
    sc["logs"] += [("frontend", "error", 17, "Error: 13 INTERNAL: Error: Product Catalog Fail Feature Flag Enabled", 4)]
    return sc


def recommendation_memory_leak():
    sc = scenario("recommendation_memory_leak", "Runaway cache in recommendation: memory grows until OOM, latency rises.",
                  {"decision": ["propose_fix", "escalate"], "services": ["recommendation"], "categories": ["resource_exhaustion"]})
    svc(sc, "recommendation", p95=420); svc(sc, "frontend", p95=95)
    op(sc, "recommendation", "oteldemo.RecommendationService/ListRecommendations", p95=420)
    op(sc, "recommendation", "get_product_list", p95=400)
    sc["mem"]["recommendation"] = 78; sc["mem_growth"]["recommendation"] = 22.0; sc["mem_ttl"]["recommendation"] = 7
    sc["traces"] += [("recommendations", None, "", 3, 650, {"demo.recommendation.cache_hit": False})] * 3
    sc["logs"] += [("recommendation", "INFO", 9, "get_product_list: cache miss", 40)]
    return sc


def ad_high_cpu():
    sc = scenario("ad_high_cpu", "High CPU load in the ad service; GetAds latency rises.",
                  {"decision": ["propose_fix", "escalate"], "services": ["ad"], "categories": ["resource_exhaustion"]})
    svc(sc, "ad", p95=950)
    op(sc, "ad", "oteldemo.AdService/GetAds", p95=950); op(sc, "frontend", "oteldemo.AdService/GetAds", p95=980)
    sc["cpu"]["ad"] = 195
    sc["traces"] += [("ads", None, "", 2, 1200, {})] * 3
    sc["logs"] += [("ad", "INFO", 9, "High CPU-Load problempattern enabled", 1)]
    return sc


def ad_gc_pauses():
    sc = scenario("ad_gc_pauses", "Heavy garbage collection in the ad service causes latency spikes.",
                  {"decision": ["propose_fix", "escalate"], "services": ["ad"], "categories": ["resource_exhaustion"]})
    svc(sc, "ad", p95=3400)
    op(sc, "ad", "oteldemo.AdService/GetAds", p95=3400)
    sc["mem"]["ad"] = 93
    sc["traces"] += [("ads", None, "", 2, 3500, {})] * 3
    sc["logs"] += [("ad", "WARN", 13, "Feature Flag adManualGc enabled, performing a manual gc now", 3),
                   ("ad", "INFO", 9, "The artificially triggered GCs took: 3412 ms", 3)]
    return sc


def homepage_flood():
    sc = scenario("homepage_flood", "Traffic flood on the homepage from the load generator.",
                  {"decision": ["propose_fix", "escalate"], "services": ["frontend", "frontend-proxy", "load-generator"],
                   "categories": ["traffic_spike"]})
    svc(sc, "frontend-proxy", rps=140, p95=620); svc(sc, "frontend", rps=130, p95=600)
    svc(sc, "product-catalog", rps=60, p95=180)
    op(sc, "frontend", "GET /", rps_mult=12, p95=600)
    sc["cpu"].update({"load-generator": 160, "frontend": 140, "frontend-proxy": 90})
    sc["traces"] += [("home", None, "", 1, 700, {})] * 4
    sc["logs"] += [("load-generator", "INFO", 9, "User flooding homepage 100 times", 30)]
    return sc


# ---- infrastructure faults ----------------------------------------------------------------------------
def container_stopped():
    sc = scenario("container_stopped", "The payment container is stopped.",
                  {"decision": ["propose_fix", "escalate"], "services": ["payment"], "categories": ["container_down"]})
    msg = ('rpc error: code = Unavailable desc = connection error: desc = "transport: Error while dialing: '
           'dial tcp: lookup payment on 127.0.0.11:53: no such host"')
    cont(sc, "payment", state="exited", status="Exited (143) 3 minutes ago", exit_code=143)
    svc(sc, "payment", rps=0.0, p95=None); svc(sc, "checkout", err=1.0)
    op(sc, "checkout", "oteldemo.PaymentService/Charge", err=1.0); op(sc, "checkout", "oteldemo.CheckoutService/PlaceOrder", err=1.0)
    sc["traces"] += [(CHAINS["checkout_payment"][:4], 3, msg, None, 0, {})] * 3
    sc["logs"] += [("checkout", "ERROR", 17, f"failed to charge card: could not charge the card: {msg}", 5)]
    return sc


def crash_loop():
    sc = scenario("crash_loop", "The cart container keeps restarting.",
                  {"decision": ["propose_fix", "escalate"], "services": ["cart"], "categories": ["container_down"]})
    cont(sc, "cart", state="restarting", status="Restarting (139) 10 seconds ago", restart_count=7, exit_code=139,
         started_min_ago=0.2)
    svc(sc, "cart", rps=0.3, err=.4); svc(sc, "frontend", err=.08)
    op(sc, "frontend", "oteldemo.CartService/GetCart", err=.6); op(sc, "frontend", "GET /api/cart", err=.6)
    sc["traces"] += [(CHAINS["get_cart"][:2], 1, "14 UNAVAILABLE: No connection established. Last error: connect ECONNREFUSED 172.18.0.12:7070", None, 0, {})] * 3
    sc["logs"] += [("frontend", "error", 17, "Error: 14 UNAVAILABLE: No connection established. Last error: connect ECONNREFUSED 172.18.0.12:7070", 8)]
    return sc


def cache_unavailable():
    sc = scenario("cache_unavailable", "The Valkey cache used by cart is down.",
                  {"decision": ["propose_fix", "escalate"], "services": ["valkey-cart"], "categories": ["cache_unavailable", "container_down", "dependency_failure"]})
    msg = ("Can't access cart storage. StackExchange.Redis.RedisConnectionException: No connection is active/available "
           "to service this operation: HGET 5f1b2c; UnableToConnect on valkey-cart:6379/Interactive")
    cont(sc, "valkey-cart", state="exited", status="Exited (0) 4 minutes ago")
    sc["redis"] = {"up": False}
    svc(sc, "cart", err=1.0); svc(sc, "frontend", err=.15); svc(sc, "checkout", err=1.0)
    for o in ("oteldemo.CartService/GetCart", "oteldemo.CartService/AddItem", "oteldemo.CartService/EmptyCart", "HGET", "HMSET"):
        op(sc, "cart", o, err=1.0)
    op(sc, "frontend", "oteldemo.CartService/GetCart", err=1.0)
    sc["traces"] += [("get_cart", -1, msg, None, 0, {})] * 4
    sc["logs"] += [("cart", "Error", 17, "Connection to Redis lost", 2),
                   ("cart", "Error", 17, "Redis connection failed: UnableToConnect on valkey-cart:6379", 5),
                   ("frontend", "error", 17, f"Error: 9 FAILED_PRECONDITION: {msg}", 10)]
    return sc


def db_unavailable():
    sc = scenario("db_unavailable", "The Postgres database (astronomy-db) behind product-catalog is down.",
                  {"decision": ["propose_fix", "escalate"], "services": ["astronomy-db"], "categories": ["dependency_failure", "container_down"]})
    msg = 'failed to query product: dial tcp 172.18.0.5:5432: connect: connection refused'
    cont(sc, "astronomy-db", state="exited", status="Exited (0) 2 minutes ago")
    svc(sc, "product-catalog", err=1.0); svc(sc, "frontend", err=.4); svc(sc, "recommendation", err=1.0)
    for o in ("oteldemo.ProductCatalogService/GetProduct", "oteldemo.ProductCatalogService/ListProducts", "SELECT catalog.products"):
        op(sc, "product-catalog", o, err=1.0)
    sc["traces"] += [("get_product", 3, msg, None, 0, {})] * 4
    sc["logs"] += [("product-catalog", "ERROR", 17, f"Error querying products: {msg}", 12)]
    return sc


def network_latency():
    sc = scenario("network_latency", "Network latency between checkout and payment (payment itself is fast).",
                  {"decision": ["propose_fix", "escalate"], "services": ["payment", "checkout"], "categories": ["latency_or_network"]})
    svc(sc, "checkout", p95=2150)
    op(sc, "checkout", "oteldemo.PaymentService/Charge", p95=2050); op(sc, "checkout", "oteldemo.CheckoutService/PlaceOrder", p95=2150)
    op(sc, "frontend", "oteldemo.CheckoutService/PlaceOrder", p95=2200)
    sc["traces"] += [("checkout_payment", None, "", 3, 2050, {})] * 4    # client span slow, payment server span fast
    return sc


def cpu_starved():
    sc = scenario("cpu_starved", "The currency service is starved of CPU (at its limit); conversions slow down.",
                  {"decision": ["propose_fix", "escalate"], "services": ["currency"], "categories": ["resource_exhaustion"]})
    svc(sc, "currency", p95=480); svc(sc, "frontend", p95=160)
    op(sc, "currency", "Currency/Convert", p95=480)
    sc["cpu"]["currency"] = 99
    return sc


def combined_payment_and_cache():
    sc = payment_failure()
    c = cache_unavailable()
    for k in ("containers", "redis"):
        sc[k] = c[k]
    for s, v in c["services"].items():
        if v["err"]:
            sc["services"][s]["err"] = max(sc["services"][s]["err"], v["err"])
    sc["ops"].update(c["ops"]); sc["traces"] += c["traces"][-4:]; sc["logs"] += c["logs"]
    sc.update(name="combined_payment_and_cache", description="Two faults at once: payment failures and the cart cache down.",
              expected={"decision": ["propose_fix", "escalate"], "services": ["payment", "valkey-cart"],
                        "categories": ["service_error", "cache_unavailable", "container_down", "dependency_failure"]})
    return sc


ALL = [base, payment_failure, cart_intermittent, product_catalog_one_product, recommendation_memory_leak, ad_high_cpu,
       ad_gc_pauses, homepage_flood, container_stopped, crash_loop, cache_unavailable, db_unavailable, network_latency,
       cpu_starved, combined_payment_and_cache]


def get(name):
    for f in ALL:
        sc = f()
        if sc["name"] == name:
            return copy.deepcopy(sc)
    raise KeyError(f"unknown scenario {name}; choose from {[f()['name'] for f in ALL]}")


def names():
    return [f()["name"] for f in ALL]
