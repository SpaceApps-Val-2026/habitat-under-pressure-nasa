"""D2 acceptance check against a deployed URL (Cloud Run or the VPS). Standard library only.

    python deploy/vps/check_deploy.py start URL "PLACE"   health, then launch one analysis through the form
    python deploy/vps/check_deploy.py after URL ID        after a redeploy: health, and the analysis is still listed

The analysis uses the form (design "trend", NASA POWER air temperature), so it needs no LLM and no key.
If the proxy asks for basic auth, set HUP_CHECK_USER and HUP_CHECK_PASSWORD in the environment; they are never
printed.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

FINISHED = ("finished", "failed")


def call(url: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"})
    user, password = os.environ.get("HUP_CHECK_USER"), os.environ.get("HUP_CHECK_PASSWORD")
    if user and password:
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        request.add_header("authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        sys.exit(f"{method} {url} -> HTTP {error.code}: {error.read()[:300]!r}")


def health(base: str) -> None:
    answer = call(f"{base}/health")
    print(f"health: status={answer.get('status')} commit={answer.get('commit')} "
          f"catalog={answer.get('catalog_version')}")
    if answer.get("status") != "ok":
        sys.exit("health is not ok")


def start(base: str, place: str) -> None:
    health(base)
    form = {"text": "Deployment check", "language": "en", "design": "trend", "place": place,
            "variables": ["air_temperature"], "period": {"start": "2000", "end": "2024"}}
    answer = call(f"{base}/api/plans", "POST", form)
    if answer.get("kind") != "plan":
        sys.exit(f"the form did not give a plan: {json.dumps(answer)[:400]}")
    analysis_id = call(f"{base}/api/analyses", "POST", {"plan": answer["plan"]})["analysis_id"]
    print(f"analysis started: {analysis_id}")
    deadline = time.monotonic() + 600
    status = None
    while time.monotonic() < deadline:
        status = call(f"{base}/api/analyses/{analysis_id}").get("status")
        if status in FINISHED:
            break
        time.sleep(5)
    print(f"analysis {analysis_id}: status={status}")
    print(f"next: redeploy, then  python deploy/vps/check_deploy.py after {base} {analysis_id}")


def after(base: str, analysis_id: str) -> None:
    health(base)
    one = call(f"{base}/api/analyses/{analysis_id}")
    status = one.get("status")
    listing = call(f"{base}/api/analyses?limit=500" + ("&status=failed" if status == "failed" else ""))
    listed = any(item.get("id") == analysis_id for item in listing.get("items", []))
    print(f"analysis {analysis_id}: status={status} listed={listed}")
    if not listed:
        sys.exit("the analysis is not in /api/analyses after the redeploy")
    print("D2 acceptance: OK")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("start", "after"):
        sys.exit(__doc__)
    base_url = sys.argv[2].rstrip("/")
    (start if sys.argv[1] == "start" else after)(base_url, sys.argv[3])
