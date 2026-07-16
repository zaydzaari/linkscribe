# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately through [GitHub Security Advisories](https://github.com/zaydzaari/linkscribe/security/advisories/new). Do not open a public issue for credentials, authentication bypasses, command injection, SSRF, or data exposure.

Include the affected version, deployment details, reproduction steps, expected impact, and any suggested mitigation. You should receive an acknowledgement within seven days.

## Supported versions

Security fixes are applied to the latest release on the `main` branch. Older snapshots are not maintained.

## Deployment responsibilities

- Terminate public traffic with HTTPS.
- Keep the bearer token out of source control, prompts, screenshots, and command-line arguments.
- Rotate the token after suspected disclosure.
- Restrict cloud firewall rules to the ports and source ranges you need.
- Keep Ubuntu, Nginx, yt-dlp, Deno, FFmpeg, and whisper.cpp current.
- Use cookies only when authorized and protect cookie files as credentials.
- Do not expose private, copyrighted, or regulated media without permission.

LinkScribe is intentionally limited to supported public-media hosts. Requests to add arbitrary URL downloading should include an SSRF threat-model review.
