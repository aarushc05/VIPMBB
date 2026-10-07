#!/usr/bin/env python3
"""
test_kinexon_api.py

Quick script to access the Georgia Tech Kinexon "Sport App API".

This Kinexon instance has two authentication layers:
  1. HTTP Basic Auth at the nginx gateway.
  2. An API key in the ``apiKey`` query parameter.

USAGE
-----
Set all credentials as environment variables (recommended):

    export KINEXON_USER="your-username"
    export KINEXON_PASSWORD="your-password"
    export KINEXON_API_KEY="your-key-here"
    python3 test_kinexon_api.py

Command-line options are also supported, but secrets supplied that way may be
visible in shell history and process listings:

    python3 test_kinexon_api.py --user USER --password PASSWORD --key API_KEY

By default this hits the Georgia Tech McCamish Kinexon instance. Override
with --base-url if needed.
"""

import argparse
import json
import os
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests.auth import HTTPBasicAuth

DEFAULT_BASE_URL = "https://georgia-tech-mccamish.access.kinexon.com"

DEFAULT_HEADERS = {
    "accept": "application/json",
    "User-Agent": "kinexon-api-test/1.0",
}

# Simple, no-path-parameter endpoint used just to test whether auth works.
TEST_PATH = "/public/v1/statistics/list"


def redact_url(url: str) -> str:
    """Redact apiKey before printing a request URL."""
    parts = urlsplit(url)
    query = [
        (name, "<redacted>" if name == "apiKey" else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def request(session, method, url, api_key, auth, timeout):
    """Make an authenticated Kinexon API request."""
    return session.request(
        method,
        url,
        auth=auth,
        params={"apiKey": api_key},
        timeout=timeout,
    )


def test_auth(base_url, api_key, auth, timeout=10.0, debug=False):
    """Test both authentication layers against the metrics-list endpoint."""
    url = base_url.rstrip("/") + TEST_PATH
    print(f"Testing auth against: {url}\n")

    if debug:
        prepared = requests.Request(
            "GET", url, headers=DEFAULT_HEADERS, params={"apiKey": api_key}
        ).prepare()
        print(f"[DEBUG] URL: {redact_url(prepared.url)}")
        print(f"[DEBUG] Headers: {dict(prepared.headers)}")
        print("[DEBUG] HTTP Basic Auth: configured (credentials redacted)")

    try:
        with requests.Session() as session:
            session.headers.update(DEFAULT_HEADERS)
            resp = request(session, "GET", url, api_key, auth, timeout)
    except requests.RequestException as exc:
        print(f"[FAIL] Request error: {exc}", file=sys.stderr)
        return None

    if resp.status_code == 200:
        print("[ OK ] HTTP Basic Auth + apiKey -> 200 OK")
        return resp

    print(f"[FAIL] HTTP Basic Auth + apiKey -> {resp.status_code} {resp.reason}")
    if resp.status_code == 401:
        challenge = resp.headers.get("WWW-Authenticate", "")
        if challenge:
            print(f"Server authentication challenge: {challenge}")
        if "Basic" in challenge:
            print("The nginx gateway rejected KINEXON_USER or KINEXON_PASSWORD.")
        else:
            print("The gateway was passed; the API key may be invalid or unauthorized.")
    elif resp.status_code == 404:
        print("Check the base URL and endpoint path.")
    if debug:
        print("Response body:")
        print(resp.text[:2000])
    return None


def pretty_print_json(resp):
    try:
        print(json.dumps(resp.json(), indent=2)[:2000])
    except ValueError:
        print(resp.text[:2000])


def run_sample_calls(base_url, api_key, auth, team_id, timeout=10.0):
    """Once we know the working auth scheme, hit a couple more endpoints."""
    print("\n--- Sample calls with working auth ---\n")

    # 1. List available metrics/events (no path params needed)
    url = base_url.rstrip("/") + "/public/v1/statistics/list"
    print(f"GET {url}")
    with requests.Session() as session:
        session.headers.update(DEFAULT_HEADERS)
        resp = request(session, "GET", url, api_key, auth, timeout)
    print(f"Status: {resp.status_code}")
    pretty_print_json(resp)

    # 2. List players for the given team ID
    url = base_url.rstrip("/") + f"/public/v1/teams/{team_id}/players"
    print(f"\nGET {url}")
    with requests.Session() as session:
        session.headers.update(DEFAULT_HEADERS)
        resp = request(session, "GET", url, api_key, auth, timeout)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 404:
        print(f"(Team ID {team_id} may not exist for this org — try a different --team-id)")
    pretty_print_json(resp)


def main():
    parser = argparse.ArgumentParser(description="Test Kinexon Sport App API auth and basic endpoints.")
    parser.add_argument(
        "--key",
        default=os.environ.get("KINEXON_API_KEY"),
        help="API key. Defaults to the KINEXON_API_KEY environment variable.",
    )
    parser.add_argument(
        "--user",
        default=os.environ.get("KINEXON_USER"),
        help="Basic Auth username. Defaults to KINEXON_USER.",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("KINEXON_PASSWORD"),
        help="Basic Auth password. Defaults to KINEXON_PASSWORD.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the Kinexon instance (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--team-id",
        type=int,
        default=3,
        help="Team ID to use for the sample /teams/{id}/players call (default: 3)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the exact outgoing request (URL and headers) for troubleshooting.",
    )
    args = parser.parse_args()

    missing = []
    if not args.user:
        missing.append("KINEXON_USER")
    if not args.password:
        missing.append("KINEXON_PASSWORD")
    if not args.key:
        missing.append("KINEXON_API_KEY")
    if missing:
        print(f"Missing required credentials: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    auth = HTTPBasicAuth(args.user, args.password)
    resp = test_auth(args.base_url, args.key, auth, debug=args.debug)

    if resp is None:
        sys.exit(2)

    print("Response body:")
    pretty_print_json(resp)

    run_sample_calls(args.base_url, args.key, auth, args.team_id)


if __name__ == "__main__":
    main()
