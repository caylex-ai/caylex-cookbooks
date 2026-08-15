# Set a Server Base URL Override

Set or clear the per-instance upstream base URL for a Caylex
Foundry-generated server. The script uses only the Python 3.10+ standard
library.

It accepts a project UUID or exact project name, plus a server instance UUID or
an exact server/display name. Name resolution inspects every API page and
rejects missing or ambiguous matches before making a change.

## Usage

Set an admin platform access token:

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

Preview a change without sending `PATCH`:

```bash
python3 set_server_base_url_override.py "Customer Support" "Internal API" \
  --set https://customer-api.example.com/v2 \
  --dry-run
```

Apply it:

```bash
python3 set_server_base_url_override.py "Customer Support" "Internal API" \
  --set https://customer-api.example.com/v2
```

Clear the override and return to the server-level default:

```bash
python3 set_server_base_url_override.py \
  7e1f2a3b-4c5d-6e7f-8a9b-0c1d2e3f4a5b \
  48c3a457-a9b0-44bd-b433-88668d417c5b \
  --clear
```

`--set` and `--clear` are mutually exclusive and one is required. The default
API root is `https://api.caylex.ai/api/v1`; use `--base-url` for another
environment.

A dry run resolves the project and server and verifies that the instance is a
Foundry server, but cannot perform the backend's full SSRF/DNS validation
without submitting the update. The backend accepts only absolute HTTP(S)
URLs, rejects placeholder and private/internal hosts, normalizes accepted
URLs, and permits overrides only for Foundry-generated servers.

## Output and secret handling

The command prints structured JSON describing the selected project, server
instance, action, previous override, effective override, and default URL. It
never prints the platform token. URL user credentials, query strings, and
fragments are removed from displayed values; this allows the exact URL to be
sent to Caylex without echoing common secret-bearing URL components.

## Test

```bash
python3 -m unittest discover -s tests -v
```
