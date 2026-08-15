# Provision Customer Projects

Clone a Caylex model project for one or more customers with
`POST /projects/from-seed`. The endpoint connects the seed's servers and
navigators, copies tool permissions, and creates one runtime API key per new
navigator instance.

## Prerequisites

- Python 3.10 or newer (standard library only)
- A Caylex platform access token with admin access
- An existing seed project configured with the desired resources and policy

Keep the token in the environment:

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

## Usage

```bash
python3 provision_customer_projects.py \
  --seed-project-name "Your Model Project" \
  --customer-name "Customer One" \
  --customer-name "Customer Two" \
  --output "./customer-runtime-keys.json"
```

Use `--seed-project-id` instead of `--seed-project-name` when appropriate.
Optional `--description`, `--icon`, and `--base-url` arguments are available.
Run `python3 provision_customer_projects.py --help` for the full CLI.

The output contains one-time navigator keys and is atomically written with mode
`0600`. Existing output files are refused so a rerun cannot overwrite secrets
that cannot be retrieved again. A project-name conflict (`409`) is treated as
an idempotent "already exists" result.

## Security and operational caveats

- Never commit, email, or paste the output file into logs. Move each key into a
  secret manager, then securely remove the local file.
- The script never prints full keys. Status output contains only customer names,
  counts, warnings, and the output path.
- The platform token is read from `CAYLEX_PLATFORM_TOKEN` by default. Avoid
  putting it directly on a command line or in shell history.
- Provisioning is incremental on the server. If a late step fails, the project
  may already exist and a retry will receive `409`. Inspect the saved response
  and warnings, then complete setup with granular endpoints or delete the
  partial project before retrying.

## Test

From this directory:

```bash
python3 -m unittest discover -s tests -v
```
