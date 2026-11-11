#!/usr/bin/env python3
"""
Setup check. Run it first (job `check`): in 1-2 minutes it tells you if every file, key, token and tool works,
so you never have to wait an hour to discover a typing mistake. It never prints your secrets.
"""
import os
import py_compile
import sys

import requests

import common as core

env = core.env
problems = []


def say(ok, text, fatal=True):
    print(("  OK       " if ok else ("  PROBLEM  " if fatal else "  WARNING  ")) + text)
    if not ok and fatal:
        problems.append(text)


print("1) files (a broken copy-paste shows up here)")
for f in ("common.py", "kids.py", "manager.py", "check.py"):
    try:
        py_compile.compile(f, doraise=True)
        say(True, f + " is a valid file")
    except Exception as e:
        say(False, f"{f} is damaged or incomplete, paste it again: {str(e)[:200]}")

print("2) secrets")
for name in ("GEMINI_API_KEY", "CF_ACCOUNT_ID", "CF_API_TOKEN", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
    say(bool(env(name)), f"secret {name} " + ("found" if env(name) else "is MISSING (Settings > Secrets and variables > Actions)"))

print("3) tools")
try:
    core.run(["ffmpeg", "-version"])
    say(True, "ffmpeg is installed")
except Exception as e:
    say(False, "ffmpeg missing: " + str(e)[:150])
for mod in ("PIL", "edge_tts", "gtts", "googleapiclient", "google.oauth2"):
    try:
        __import__(mod)
        say(True, "python package " + mod)
    except Exception as e:
        say(False, f"python package {mod} missing - check requirements.txt: {e}")

print("4) Gemini")
if env("GEMINI_API_KEY"):
    try:
        core._gemini_only('Reply with exactly this JSON: {"ok": true}')
        say(True, f"Gemini answered (model {core._models['good']})")
    except Exception as e:
        msg = str(e)
        bad_key = "API key" in msg or "HTTP 400" in msg or "HTTP 403" in msg
        say(False, "Gemini: " + msg[:220] + ("" if bad_key else "  (limit only: the Cloudflare text model will write instead)"), fatal=bad_key)

print("5) Cloudflare pictures")
if env("CF_API_TOKEN") and env("CF_ACCOUNT_ID"):
    try:
        import kids
        data = kids.img_cloudflare("a small red apple, children's picture-book illustration", 1)
        say(len(data) > 1000, f"Cloudflare drew a test picture ({len(data) // 1024} KB, model {kids._cf['model']})")
    except Exception as e:
        if e.__class__.__name__ == "ImageQuota":
            say(False, "Cloudflare daily allowance is used up today; the keyless picture service will be used", fatal=False)
        else:
            say(False, "Cloudflare picture failed: " + str(e)[:300])

print("6) YouTube")
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN"), token_uri="https://oauth2.googleapis.com/token",
                        client_id=env("YT_CLIENT_ID"), client_secret=env("YT_CLIENT_SECRET"))
    creds.refresh(Request())
    got = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": creds.token}, timeout=30).json().get("scope", "").split()
    for short in ("youtube", "youtube.force-ssl", "youtube.upload"):
        say("https://www.googleapis.com/auth/" + short in got, f"token has the scope {short}")
    name = core.youtube().channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
    say(True, f"logged in to the YouTube channel: {name}   <- it must be your KIDS channel")
except Exception as e:
    say(False, "YouTube login failed (check YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN): " + repr(e)[:300])

print()
if problems:
    print("RESULT: fix these first, then run the check again:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("RESULT: everything is ready. Next: job kids_short, mode dry.")

