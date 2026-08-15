# Export Session Messages

Export every assistant chat session for a project and every message in each
session as structured JSON. The script uses only the Python 3.10+ standard
library.

It resolves either a project UUID or an exact, case-sensitive project name,
fetches all matching session-list pages, then follows the opaque cursor for
every session's complete message timeline.

## Usage

Set an admin platform access token in the environment:

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

Export a date range:

```bash
python3 get_session_messages.py "Customer Support" \
  --start 2026-08-01T00:00:00Z \
  --end 2026-08-15T23:59:59Z \
  --view resolved \
  --output exports/customer-support.json
```

Both date bounds are inclusive and must be ISO 8601 date-times with a timezone.
`--view raw` preserves Caylex meta-tool events. `--view resolved` removes those
meta-tools and unwraps `invoke_tools` into downstream tool calls and results.

Optional filters:

```bash
python3 get_session_messages.py 7e1f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b \
  --navigator-instance-id 9a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d \
  --production \
  --output exports/production.json
```

Use `--playground` for playground sessions. Omit both `--playground` and
`--production` to include either type. The default API root is
`https://api.caylex.ai/api/v1`; use `--base-url` for another environment.

The output is written through a temporary file and atomically renamed. Existing
files are not replaced unless `--force` is supplied. The status printed to
stdout contains only the output path, project ID, and session count; the token
is never printed.

## Output

The JSON document contains:

- the resolved project ID and name;
- the filters and selected message view;
- each session's list summary and detailed session metadata; and
- all message rows, with their typed timeline events, in API order.

## Test

```bash
python3 -m unittest discover -s tests -v
```
