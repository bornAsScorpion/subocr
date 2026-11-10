# Contributing

Thanks for taking an interest. Bug reports, ideas and pull requests are all
welcome.

## Reporting a problem

Open an issue with:

- what you expected and what happened;
- the relevant lines from `docker logs subocr`;
- for a specific file, the output of
  `docker exec subocr subocr probe "/path/to/file.mkv"`;
- your subocr version (`GET /api/v1/system/status`, or the image tag).

Please leave API keys out of anything you paste.

## Working on the code

You need Python 3.11 or newer. The unit tests need no FFmpeg or Tesseract.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && pytest
```

To try a change end to end, build the image and run it against a copy of a few
media files:

```bash
docker build -t subocr:local .
docker run --rm -v /path/to/sample:/data:ro subocr:local probe "/data/sample.mkv"
```

## Pull requests

- Keep changes focused, one topic per pull request.
- Add or update tests for behaviour changes; real-world file layouts make the
  best fixtures.
- Update the docs and add a line under an *Unreleased* heading in
  `CHANGELOG.md` when users would notice the change.
- `ruff check .` and `pytest` must pass. CI runs both.
