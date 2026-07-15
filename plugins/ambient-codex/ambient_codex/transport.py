"""HTTP request primitives and defensive model-catalog normalization.

The public CLI owns policy: retry timing, terminal diagnostics, process exits,
and catalog memoization remain at its facade.  This module owns the transport
operation itself and is deliberately dependency-injectable so callers retain
their existing test seams without import-time network or state effects.
"""

import http.client
import json
import sys
import time
import urllib.error
import urllib.request

from ambient_codex.records import NetworkError


def _default_retry_delay(base, headers=None):
    """Use the requested base delay when no CLI retry policy is supplied."""
    del headers
    return base


def api_request(api_url, api_key, path, payload=None, timeout=300, *,
                retry_delay=None, sleep=None, stderr=None, opener=None):
    """Make one JSON API request and return ``(status, body)``.

    Only idempotent GETs are retried.  POSTs may already have completed when a
    connection fails, so retrying one could duplicate a billable completion.
    Dependencies are optional to keep this lower layer usable in isolation;
    the CLI facade supplies its established retry policy and streams.
    """
    retry_delay = retry_delay or _default_retry_delay
    sleep = sleep or time.sleep
    stderr = stderr or sys.stderr
    opener = opener or urllib.request.urlopen
    url = f"{api_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST" if data else "GET",
    )
    retryable = data is None
    last_error = NetworkError(f"cannot reach {url}")
    for attempt in (1, 2):
        try:
            with opener(request, timeout=timeout) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code in (502, 503, 504) and attempt == 1 and retryable:
                print(f"ambient: HTTP {error.code}, retrying once...", file=stderr)
                sleep(retry_delay(2, error.headers))
                continue
            try:
                return error.code, json.loads(body)
            except json.JSONDecodeError:
                return error.code, {"error": {"message": body[:2000]}}
        except urllib.error.URLError as error:
            last_error = NetworkError(f"cannot reach {url} ({error.reason})")
        except (OSError, http.client.HTTPException) as error:
            last_error = NetworkError(f"read from {url} failed ({error})")
        except json.JSONDecodeError:
            last_error = NetworkError(f"{url} returned malformed JSON")
        if attempt == 1 and retryable:
            print("ambient: network blip, retrying once...", file=stderr)
            sleep(retry_delay(2))
        else:
            break
    raise last_error


def catalog_data(body):
    """Return valid model-object rows from an untrusted ``/v1/models`` body."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [entry for entry in data
            if isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry["id"]]


__all__ = ("api_request", "catalog_data")
