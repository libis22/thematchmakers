"""
The Matchmakers — proxy Lambda.

Thin wrapper between the chat SPA and the already-deployed AgentCore Harness
(provider_matching_assistant). It does nothing clever: validate the request,
call invoke_harness, drain the streaming response into one string, return it
as JSON. All the actual reasoning / tool-calling / formatting happens inside
the Harness itself (Gateway -> tools Lambda -> Athena / KB / Location).

Deliberately not real HTTP streaming (v1). The boto3 call already returns
delta events as they arrive, so upgrading this to a real streaming response
later (e.g. via the Lambda Web Adapter + FastAPI StreamingResponse) only
means changing how this handler emits `text` as it's produced instead of
buffering it in `_invoke_harness`. The frontend currently fakes the
typing effect client-side once the full reply lands.

Env vars:
  HARNESS_ARN     (required) ARN of the Harness to invoke
  SHARED_SECRET   (optional) if set, requests must send header
                  x-matchmaker-secret matching this value. This is a basic
                  deterrent for an internal demo URL, not real auth —
                  anyone with the value (or the page's source) can read it.
                  Swap in Cognito if real access control is ever needed.
"""

import json
import os
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
HARNESS_ARN = os.environ["HARNESS_ARN"]
SHARED_SECRET = os.environ.get("SHARED_SECRET", "")

client = boto3.client("bedrock-agentcore", region_name=REGION)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,x-matchmaker-secret",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _invoke_harness(message, session_id):
    """Call the Harness and drain the stream into a single string.

    The stream can carry other event types (tool-use blocks, message
    start/stop, etc). We only care about contentBlockDelta.delta.text for
    v1 — everything else is ignored, not an error.
    """
    resp = client.invoke_harness(
        harnessArn=HARNESS_ARN,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
    )
    text = ""
    for event in resp["stream"]:
        delta = (event.get("contentBlockDelta") or {}).get("delta") or {}
        if "text" in delta:
            text += delta["text"]
    return text


def lambda_handler(event, context):
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "POST")
    if method == "OPTIONS":
        # Function URL CORS config handles the browser preflight in most cases;
        # this is a fallback so a stray OPTIONS never 500s.
        return _response(200, {})

    if SHARED_SECRET:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-matchmaker-secret") != SHARED_SECRET:
            return _response(401, {"error": "Unauthorized"})

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    message = (payload.get("message") or "").strip()
    if not message:
        return _response(400, {"error": "message is required"})

    # The browser generates and keeps this per conversation (sessionStorage) so
    # the Harness sees the same runtimeSessionId across turns and keeps context.
    session_id = (payload.get("session_id") or "").strip() or str(uuid.uuid4())

    try:
        reply = _invoke_harness(message, session_id)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI, logged for debugging
        print(f"ERROR invoking harness: {e!r}")
        return _response(502, {"error": "The assistant could not be reached. Please try again."})

    if not reply:
        print(f"WARN: empty reply from harness for session {session_id}")
        reply = "Sorry, I didn't get a response that time — please try again."

    return _response(200, {"reply": reply, "session_id": session_id})
