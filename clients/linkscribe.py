#!/usr/bin/env python3
"""Zero-dependency LinkScribe client for humans and coding agents."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class ClientError(RuntimeError):
    pass


class JobFailedError(ClientError):
    def __init__(self, job: dict) -> None:
        self.job = job
        super().__init__(job.get("error") or f"Job {job['status']}")


def _is_youtube_bot_challenge(url: str, error: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    is_youtube = hostname in {"youtu.be", "youtube.com"} or hostname.endswith(
        ".youtube.com"
    )
    normalized_error = error.lower()
    return (
        is_youtube
        and "sign in to confirm" in normalized_error
        and "bot" in normalized_error
    )


def _parse_youtube_json3(path: Path) -> str:
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ClientError("Public caption response is unexpectedly large")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClientError("Public captions were not valid JSON") from exc

    parts: list[str] = []
    for event in payload.get("events", []):
        segments = event.get("segs", []) if isinstance(event, dict) else []
        text = "".join(
            str(segment.get("utf8", ""))
            for segment in segments
            if isinstance(segment, dict)
        )
        text = " ".join(text.split())
        if text and (not parts or text != parts[-1]):
            parts.append(text)

    transcript = " ".join(parts)
    if not transcript:
        raise ClientError("Public captions contained no spoken text")
    return transcript


def fetch_local_youtube_captions(url: str) -> str:
    configured_binary = os.getenv("LINKSCRIBE_LOCAL_YTDLP_BIN", "").strip()
    ytdlp = configured_binary or shutil.which("yt-dlp")
    if not ytdlp:
        raise ClientError("Local yt-dlp is required for the public-caption fallback")

    with tempfile.TemporaryDirectory(prefix="linkscribe-captions-") as temp_dir:
        output = Path(temp_dir) / "captions.%(ext)s"
        command = [
            ytdlp,
            "--ignore-config",
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en-orig,en",
            "--sub-format",
            "json3",
            "--output",
            str(output),
            url,
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClientError("Local public-caption retrieval failed") from exc

        if result.returncode != 0:
            raise ClientError("Local public-caption retrieval failed")

        candidates = sorted(
            Path(temp_dir).glob("captions.*.json3"),
            key=lambda path: (".en-orig." not in path.name, path.name),
        )
        if not candidates:
            raise ClientError("This video has no accessible public English captions")

        errors: list[ClientError] = []
        for candidate in candidates:
            try:
                return _parse_youtube_json3(candidate)
            except ClientError as exc:
                errors.append(exc)
        raise errors[-1]


class LinkScribeClient:
    def __init__(self, base_url: str, token: str, timeout: int = 35) -> None:
        parsed = urlsplit(base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
        if parsed.scheme != "https" and not local_http:
            raise ClientError("LINKSCRIBE_API_URL must use HTTPS")
        unsafe_parts = (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        )
        if unsafe_parts:
            raise ClientError("LINKSCRIBE_API_URL must be a clean API origin")
        if parsed.path not in {"", "/"}:
            raise ClientError("LINKSCRIBE_API_URL must not contain a path")
        if not token:
            raise ClientError("LINKSCRIBE_API_TOKEN is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(  # noqa: S310 - base URL is validated as HTTPS or loopback HTTP.
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "LinkScribe-Client/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read())
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", exc.reason)
            except (json.JSONDecodeError, AttributeError):
                detail = exc.reason
            raise ClientError(f"API error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ClientError(f"Could not reach LinkScribe: {exc.reason}") from exc

    def submit(self, url: str) -> dict:
        return self.request("POST", "/v1/jobs", {"url": url})

    def status(self, job_id: str, wait_seconds: int = 0) -> dict:
        query = urlencode({"wait_seconds": wait_seconds})
        return self.request("GET", f"/v1/jobs/{job_id}?{query}")

    def transcript(self, job_id: str) -> str:
        offset = 0
        chunks: list[str] = []
        while True:
            query = urlencode({"offset": offset, "limit": 20000})
            data = self.request("GET", f"/v1/jobs/{job_id}/transcript?{query}")
            chunks.append(data["text"])
            if data["next_offset"] is None:
                return "".join(chunks)
            offset = data["next_offset"]

    def wait(self, job_id: str, max_wait: int = 7200) -> dict:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            job = self.status(job_id, wait_seconds=25)
            print(f"LinkScribe job {job_id}: {job['status']}", file=sys.stderr)
            if job["status"] == "completed":
                return job
            if job["status"] in {"failed", "cancelled"}:
                raise JobFailedError(job)
        raise ClientError(f"Job did not finish within {max_wait} seconds")


def transcribe_url(
    client: LinkScribeClient,
    url: str,
    *,
    max_wait: int = 7200,
    local_caption_fallback: bool = True,
) -> str:
    created = client.submit(url)
    try:
        client.wait(created["id"], max_wait=max_wait)
    except JobFailedError as exc:
        if not local_caption_fallback or not _is_youtube_bot_challenge(url, str(exc)):
            raise
        print(
            "VPS download was challenged by YouTube; trying public captions locally.",
            file=sys.stderr,
        )
        try:
            return fetch_local_youtube_captions(url)
        except ClientError as fallback_error:
            raise ClientError(f"{exc}; {fallback_error}") from fallback_error
    return client.transcript(created["id"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.getenv("LINKSCRIBE_API_URL", ""),
        help="API base URL; defaults to LINKSCRIBE_API_URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("LINKSCRIBE_API_TOKEN", ""),
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="Create a transcription job")
    submit.add_argument("url")

    check = subparsers.add_parser("status", help="Read a job")
    check.add_argument("job_id")

    transcribe = subparsers.add_parser("transcribe", help="Submit, wait, and print transcript")
    transcribe.add_argument("url")
    transcribe.add_argument("--output", type=Path)
    transcribe.add_argument("--max-wait", type=int, default=7200)
    transcribe.add_argument(
        "--no-local-caption-fallback",
        action="store_true",
        help="Do not use public local YouTube captions after a VPS bot challenge",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = LinkScribeClient(args.api_url, args.token)
        if args.command == "submit":
            print(json.dumps(client.submit(args.url), indent=2))
        elif args.command == "status":
            print(json.dumps(client.status(args.job_id), indent=2))
        else:
            transcript = transcribe_url(
                client,
                args.url,
                max_wait=args.max_wait,
                local_caption_fallback=not args.no_local_caption_fallback,
            )
            if args.output:
                args.output.write_text(transcript + "\n", encoding="utf-8")
                print(str(args.output))
            else:
                print(transcript)
        return 0
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
