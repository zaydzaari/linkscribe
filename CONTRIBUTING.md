# Contributing

Thanks for improving LinkScribe.

1. Fork the repository and create a focused branch.
2. Install the development dependencies with `python -m pip install -e ".[dev]"`.
3. Add or update tests for behavior changes.
4. Run `ruff check .` and `python -m pytest --cov=linkscribe --cov=clients`.
5. Open a pull request that explains the user-visible effect and deployment impact.

Keep changes small and compatible with Python 3.10. Never commit API tokens, cookies, transcripts, downloaded media, VPS addresses, or other deployment secrets. New download sources must be reviewed for SSRF, authentication, copyright, privacy, storage, and resource-exhaustion risks.
