"""Shared helpers: environment, FFmpeg, Gemini (with a Cloudflare text backup) and the YouTube login."""
import json
import os
import re
import subprocess
import time

import requests


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


MODE = env("MODE", "dry")  # dry | private | publish


class QuotaStop(Exception):
    pass


def run(cmd, cwd=None):
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("command failed: " + " ".join(map(str, cmd))[:200] + "\n" + p.stderr[-1500:])
    return p.stdout


def duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


GEMINI = "https://generativelanguage.googleapis.com/v1beta"


_models = {"list": None, "good": None}


def gemini_models():
    """Model names change often, so we ask the API which ones exist."""
    if env("GEMINI_MODEL"):
        return [env("GEMINI_MODEL")]
    if _models["list"] is None:
        r = requests.get(f"{GEMINI}/models", params={"pageSize": 200},
                         headers={"x-goog-api-key": env("GEMINI_API_KEY")}, timeout=30)
        r.raise_for_status()
        names = []
        for m in r.json().get("models", []):
            n = m.get("name", "").split("/")[-1]
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if "flash" not in n:
                continue
            if any(x in n for x in ("image", "tts", "live", "audio", "embed", "exp", "robotics", "computer")):
                continue
            names.append(n)

        def rank(n):
            v = re.search(r"(\d+(?:\.\d+)?)", n)
            return ("lite" in n, "preview" in n, -(float(v.group(1)) if v else 0.0))

        _models["list"] = sorted(set(names), key=rank)
        print("  gemini models:", _models["list"][:6])
    return _models["list"]


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _gemini_only(prompt):
    models = gemini_models()
    if _models["good"] in models:
        models = [_models["good"]] + [m for m in models if m != _models["good"]]
    last = "no model available"
    for model in models[:4]:
        for attempt in range(2):
            try:
                r = requests.post(
                    f"{GEMINI}/models/{model}:generateContent",
                    headers={"x-goog-api-key": env("GEMINI_API_KEY"), "Content-Type": "application/json"},
                    json={"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 1.0,
                                               "responseMimeType": "application/json",
                                               "maxOutputTokens": 8192}},
                    timeout=120)
                if r.status_code == 429:
                    kind = "daily quota used up" if "PerDay" in r.text else "per-minute limit"
                    last = f"{model}: rate limited ({kind}) {r.text[:160]}"
                    if "limit: 0" in r.text or "PerDay" in r.text:  # not in the free tier, or today's quota is gone
                        break
                    time.sleep(15)
                    continue
                if r.status_code >= 400:
                    last = f"{model}: HTTP {r.status_code} {r.text[:160]}"
                    break
                data = r.json()
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                obj = parse_json(text)
                _models["good"] = model
                time.sleep(6)  # stay under free-tier requests/minute
                return obj
            except Exception as e:  # bad JSON, network, blocked answer...
                last = f"{model}: {e!r}"[:200]
                time.sleep(3)
    raise RuntimeError("Gemini failed: " + last)


CF_TEXT_MODELS = ["@cf/meta/llama-3.1-8b-instruct", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"]


def cf_llm_json(prompt):
    """Backup writer on the free Cloudflare account when Gemini is rate limited."""
    base = f"https://api.cloudflare.com/client/v4/accounts/{env('CF_ACCOUNT_ID')}/ai"
    hdr = {"Authorization": f"Bearer {env('CF_API_TOKEN')}"}
    models = list(CF_TEXT_MODELS)
    try:
        r = requests.get(base + "/models/search", headers=hdr, params={"task": "Text Generation", "per_page": 100}, timeout=30)
        listed = [m.get("name") for m in r.json().get("result", []) if isinstance(m, dict)]
        models = [m for m in CF_TEXT_MODELS if m in listed] or models
    except Exception:
        pass
    last = "no model answered"
    for m in models:
        try:
            r = requests.post(f"{base}/run/{m}", headers=hdr, timeout=240, json={
                "messages": [{"role": "system", "content": "Reply with ONLY one valid JSON object. No markdown, no commentary."},
                             {"role": "user", "content": prompt}],
                "max_tokens": 4096, "temperature": 0.9})
            if r.status_code >= 400:
                last = f"{m}: HTTP {r.status_code} {r.text[:160]}"
                continue
            res = r.json().get("result", {})
            text = res.get("response") if isinstance(res, dict) else None
            if text is None and isinstance(res, dict) and res.get("choices"):
                text = res["choices"][0]["message"]["content"]
            if isinstance(text, (dict, list)):
                return text
            return parse_json(text or "")
        except Exception as e:
            last = f"{m}: {e!r}"[:200]
    raise RuntimeError("Cloudflare text fallback failed: " + last)


def gemini_json(prompt):
    try:
        return _gemini_only(prompt)
    except Exception as e:
        if env("CF_ACCOUNT_ID") and env("CF_API_TOKEN"):
            print("  Gemini unavailable (" + str(e)[:150] + ") -> using the Cloudflare text model")
            return cf_llm_json(prompt)
        raise


def youtube():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(None, refresh_token=env("YT_REFRESH_TOKEN"),
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=env("YT_CLIENT_ID"), client_secret=env("YT_CLIENT_SECRET"))
    return build("youtube", "v3", credentials=creds, cache_discovery=False)
