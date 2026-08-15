# Caylex Cookbooks

Practical integration recipes and reference scripts for building with Caylex.

## Cookbooks

- [Provision customer projects](./provision-customer-projects/) — clone a model
  project and securely capture each navigator's one-time runtime key.
- [Generate navigator API keys](./generate-navigator-api-keys/) — idempotently
  mint runtime keys for the navigator instances in a project.
- [Copy tool permissions](./copy-tool-permissions/) — replicate a navigator's
  tool policy from a model project to existing customer projects.
- [Sync skills](./sync-skills/) — reconcile repository-managed project or
  navigator-global skills with Caylex.
- [Search skills by tag](./search-skills-by-tag/) — search project and
  navigator-global skills by exact tag.
- [Get session messages](./get-session-messages/) — export complete assistant
  session timelines for a project and date range.
- [Background agent file sync](./background-agent-file-sync/) — collect Notion
  content with a background agent and convert its trace to resource-centric
  JSON.
- [Set a server base URL override](./set-server-base-url-override/) — point one
  project's Foundry server instance at a project-specific upstream URL.

Each directory is self-contained and includes its own usage guide, script, and
tests. The examples use Python 3.10 or newer and keep credentials in environment
variables.

## Authentication

Set a server-side platform access token before running a cookbook:

```bash
export CAYLEX_PLATFORM_TOKEN="your_platform_access_token"
```

Platform access tokens have workspace-wide administrative access. Store them in
a secret manager, use them only in trusted backend or CI environments, and
never expose them to browser code or commit them to source control.

Unless a cookbook says otherwise, scripts target
`https://api.caylex.ai/api/v1`. Use their `--base-url` option for another
environment.

## Run all tests

The cookbooks use the Python standard library and require no package install:

```bash
for tests in */tests; do
  (cd "$(dirname "$tests")" && python3 -m unittest discover -s tests -v) || exit 1
done
```
