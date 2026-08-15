# Generate Navigator API Keys

Create one runtime API key for every navigator instance in a project. Existing
keys with the requested name are detected across all API pages and skipped, so
the command is safe to rerun.

## Prerequisites

- Python 3.10 or newer (standard library only)
- A Caylex platform access token with admin access
- A project with at least one connected navigator

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

## Secure file output

```bash
python3 generate_navigator_api_keys.py \
  --project-name "Your Customer Project" \
  --key-name "production-runtime" \
  --output "./navigator-runtime-keys.json"
```

The file is atomically written with mode `0600`, and an existing path is never
overwritten. Use `--project-id` instead of `--project-name` if desired. Optional
`--description`, `--expires-at`, and `--base-url` arguments are available.

For deliberate piping directly into a secret-management command, explicitly
opt in to secret output:

```bash
python3 generate_navigator_api_keys.py \
  --project-id "your-project-id" \
  --key-name "production-runtime" \
  --print-secrets | your-secret-import-command
```

`--print-secrets` streams newline-delimited JSON to stdout. Each key is emitted
immediately as an `event: "created"` record, followed by an
`event: "complete"` summary; status messages remain on stderr. Streaming each
one-time key immediately prevents a later navigator failure from hiding keys
that were already created. Do not use this mode in terminals or CI systems that
retain stdout.

## Security caveats

- Caylex returns each full key only once. The script therefore requires either
  a secure output path or the explicit stdout opt-in.
- Never commit or log the output. Move it to a secret manager and securely
  remove the local file.
- Key-name idempotence prevents accidental duplicates but cannot recover a lost
  key. Revoke a lost key and create a new one under a new name.
- The platform token is read from `CAYLEX_PLATFORM_TOKEN` by default; do not put
  it in command arguments.

## Test

From this directory:

```bash
python3 -m unittest discover -s tests -v
```
