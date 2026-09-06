# Live GitHub-to-Devin demonstration

This runbook demonstrates one complete, governed path: a qualified backlog
issue becomes a human-reviewable pull request. It uses a temporary HTTPS tunnel
for a local demonstration, not a production deployment.

Complete [Setup and local run](setup.md) first. In particular, use a v3 `cog_`
service-user credential and unique values for `GITHUB_WEBHOOK_SECRET` and
`CONTROL_PLANE_API_KEY`. Do not put those values in a screenshot, Loom, or
commit.

## 1. Start the control plane

In one terminal, from the repository root:

```bash
docker compose up --build
```

In a second terminal, wait for the health check:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

Starting the service alone does not create a Devin session. A qualifying GitHub
label event is required.

## 2. Expose the local webhook over HTTPS

Choose one option and leave that command running in its own terminal.

### Option A: Cloudflare Quick Tunnel

Cloudflare Quick Tunnel is fast and accountless:

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000
```

Copy the temporary `https://<random>.trycloudflare.com` URL printed by the
command. Quick Tunnel URLs change each run and have no uptime guarantee, so use
them for a demonstration only.

### Option B: ngrok

ngrok requires an ngrok account:

```bash
# One-time setup, if required by your ngrok account:
ngrok config add-authtoken <your-ngrok-authtoken>

# Start the temporary HTTPS endpoint:
ngrok http 8000
```

Copy the HTTPS forwarding URL displayed by ngrok. Do not commit or record the
ngrok authentication token.

Confirm either public URL reaches the service before configuring GitHub:

```bash
curl --fail --silent --show-error https://<public-url>/health
```

## 3. Configure the fork webhook

In the target fork, open **Settings → Webhooks → Add webhook** and enter:

- **Payload URL:** `https://<public-url>/webhooks/github`
- **Content type:** `application/json`
- **Secret:** the local value of `GITHUB_WEBHOOK_SECRET`
- **Events:** choose **Let me select individual events**, then select
  **Issues** only
- **Active:** enabled

Save the webhook. GitHub should show a successful ping delivery. The endpoint
accepts only correctly HMAC-signed payloads, and it acts only on the configured
repository, label, and `issues.labeled` event.

## 4. Trigger and inspect one qualified issue

On a prepared, low-risk issue in the fork, add the configured `devin-autofix`
label. That label event creates one durable job and causes the worker to request
a Devin session through the v3 API. Do not manually start a separate Devin
coding session for this run.

Use the operator endpoints to inspect the durable job record and pilot metrics:

```bash
set -a
source .env
set +a

curl --silent --show-error \
  -H "Authorization: Bearer $CONTROL_PLANE_API_KEY" \
  http://127.0.0.1:8000/jobs | jq

curl --silent --show-error http://127.0.0.1:8000/metrics | jq
```

Also inspect the webhook delivery history in GitHub, the linked Devin session,
and the pull request Devin opens in the fork. The control plane never merges a
pull request; review and merge remain human decisions.

## 5. Stop the demonstration

Press `Ctrl-C` in the tunnel terminal, then stop the container:

```bash
docker compose down
```

`docker compose down` preserves the named SQLite volume. Use `docker compose
down -v` only when you intentionally want to delete the local demonstration
history.
