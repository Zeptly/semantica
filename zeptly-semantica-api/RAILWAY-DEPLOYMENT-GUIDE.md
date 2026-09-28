# Railway Deployment Guide: zeptly-semantica

This guide deploys the derived semantic projection service to Railway using the dashboard. It was written in Phase 6.25B **without access to Railway**. Dashboard labels are described by function. Railway renames menu items from time to time, so if a label differs, look for the control that does the described job.

Target topology (exactly):

```
Railway project: zeptly-semantica   (one environment: production)
├── semantica-api   GitHub source → Dockerfile build → /health healthcheck
└── falkordb        Docker image falkordb/falkordb-server:v4.14.8
    └── volume      mounted at /var/lib/falkordb/data
```

It has no worker, no other database and no vector store. FalkorDB never gets a public domain or TCP proxy.

## Value provenance legend

Every value below is marked with one of these:

| Mark | Meaning |
|---|---|
| **[GENERATE]** | You generate it locally. It is a secret. Paste it only into Railway variables. Never put it in Git, docs, tickets or chat. |
| **[RAILWAY]** | Railway supplies it automatically. Do not set it yourself. |
| **[REFERENCE]** | A Railway reference variable that copies a value from the FalkorDB service, for example `${{falkordb.RAILWAY_PRIVATE_DOMAIN}}`. |
| **[FIXED]** | A non-secret setting. Enter it exactly as shown. |

---

## 1. Create the Railway project

1. In the Railway dashboard, click **New Project**, then choose **Empty Project**.
2. Open the project's **Settings** and set the **Project Name** to `zeptly-semantica` **[FIXED]**.
3. Keep the single default environment, `production`. If your workspace creates environments for pull requests, turn that off for this project, because the graph volume must not be duplicated into ephemeral environments.
4. Note the environment's creation date. Environments created **before 16 October 2025** have IPv6-only private networking; only those need `SEMANTICA_BIND_HOST=::` (step 9). Newer environments are dual-stack and use the default `0.0.0.0`, and the check in step 14 confirms it.

## 2. Create the FalkorDB service

1. On the project canvas, click **Create** (or **+ New**), then **Docker Image**.
2. For the image, enter `falkordb/falkordb-server:v4.14.8` **[FIXED]**. See step 3 for digest pinning.
3. After the service appears, open **Settings** and set the **Service Name** to `falkordb` **[FIXED]**. The other service's reference variables depend on this exact name.
4. Do **not** deploy yet. Configure the volume and variables first (steps 4–7). If Railway starts a deployment automatically, that's harmless: it redeploys after you change variables.

Why `falkordb-server` rather than `falkordb/falkordb`? The `falkordb/falkordb` image also runs a browser UI on port 3000. The `-server` image runs only the database.

## 3. Pin the FalkorDB image

* The pinned version is **FalkorDB v4.14.8**, digest `sha256:71afd8c7cc44c15caab5fb35343d46bb0dc6ce3ee80a33f599f5d6647cdb50c4` **[FIXED]**.
* Preferred: set the source image to `falkordb/falkordb-server:v4.14.8@sha256:71afd8c7cc44c15caab5fb35343d46bb0dc6ce3ee80a33f599f5d6647cdb50c4`. If Railway rejects a digest reference, use the tag `falkordb/falkordb-server:v4.14.8` and record in the Phase 6.25C report the digest Railway pulled, which is shown in the deployment details.
* Never use `latest`, `edge` or an unversioned tag.
* Make sure **automatic image updates** are off for this service. They are off by default; if the service Settings shows an option to update the image automatically, leave it disabled.

## 4. Attach the persistent volume

1. Right-click the `falkordb` service on the canvas and choose **Attach Volume**. If that option isn't there, open the command palette with ⌘K or Ctrl+K and search for "volume".
2. Set the **Mount path** to `/var/lib/falkordb/data` **[FIXED]**. This is the image's `FALKORDB_DATA_PATH`: Redis runs with `--dir /var/lib/falkordb/data`, and the RDB and AOF files are written there.
   * Do **not** use `/data`. The image declares `/data` as a volume, but FalkorDB does not write there.
3. Confirm the volume appears attached to `falkordb` on the canvas. 1 GB is ample for Phase 6.25.

## 5. Generate the secrets locally **[GENERATE]**

Run these on your own machine, not in a shared terminal recording:

```sh
# API key used by Zeptly (and by you during validation). 64 URL-safe characters.
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# FalkorDB password. Hex only, so it never needs quoting in REDIS_ARGS.
python3 -c "import secrets; print(secrets.token_hex(32))"
```

* Keep the values in a password manager.
* The API key must be at least 32 characters; the service refuses to start otherwise.
* Do not reuse keys from other systems.

## 6. Configure FalkorDB variables

Open `falkordb`, go to **Variables**, and add:

| Variable | Value | Kind |
|---|---|---|
| `FALKORDB_PASSWORD` | the hex password from step 5 | **[GENERATE]** secret |
| `REDIS_ARGS` | `--appendonly yes --appendfsync everysec --requirepass ${{FALKORDB_PASSWORD}}` | **[FIXED]**, with a same-service reference |

`REDIS_ARGS` is read by the image's `run.sh`. It turns on append-only-file persistence (plus the default RDB snapshots) and requires the password.

* **Do not set `FALKORDB_ARGS`.** The image default (`MAX_QUEUED_QUERIES 25 TIMEOUT 1000 RESULTSET_SIZE 10000`) is the tested configuration.
* **Do not set `BROWSER`.** The server image has no browser.

**[RAILWAY]** Railway provides `RAILWAY_PRIVATE_DOMAIN` and the other `RAILWAY_*` variables automatically.

## 7. Get FalkorDB's private hostname and keep it private

1. Open `falkordb`, then **Settings**, then **Networking**.
2. Under **Private Networking**, copy the DNS name. It will be `falkordb.railway.internal` **[RAILWAY]**. Record the exact value for the Phase 6.25C report.
3. Under **Public Networking**, make sure there is **no domain and no TCP proxy**. If Railway suggested or created one, delete it. This is the "keep FalkorDB private" requirement in step 11.
4. Deploy `falkordb`: use **Deploy** or apply the staged changes.
5. Open **Deployments** and **View logs**. Success looks like:
   * `<graph> Starting up FalkorDB version 4.14.8`
   * `Ready to accept connections tcp`
   * On a redeploy, the log also shows the AOF being loaded. On the very first boot there is nothing to load.

A warning that says `Memory overcommit must be enabled` is expected. Railway doesn't let containers change kernel settings. It only matters for background saves under memory pressure, which a graph of this size won't hit.

## 8. Create `semantica-api` from GitHub

1. On the canvas, click **Create** (or **+ New**), then **GitHub Repo**, and choose `Zeptly/semantica`.
2. Under **Settings**:
   * Set the **Service Name** to `semantica-api` **[FIXED]**.
   * Under **Source**, set the branch to `main` **[FIXED]**. That is the branch the Phase 6.25 PR merges into. To test before merging, choose the PR branch temporarily and return to `main` afterwards.
   * Under **Source**, set the **Root Directory** to `/zeptly-semantica-api` **[FIXED]**.
   * Under **Config-as-code**, set the **Railway Config File** path to `/zeptly-semantica-api/railway.toml` **[FIXED]**. Railway does not look for `railway.toml` under the Root Directory on its own, so this absolute path is required.
3. After setting the config file path, check that the service shows the values that `railway.toml` sets:

   | Setting | Expected value |
   |---|---|
   | Builder | Dockerfile |
   | Watch paths | `/zeptly-semantica-api/**` |
   | Healthcheck path | `/health` |
   | Healthcheck timeout | 60 |
   | Restart policy | On Failure, 10 retries |
   | Replicas | 1 |

   Values that come from the config file are shown as locked or managed by the file.
4. Leave the **Start Command** empty. The image's `ENTRYPOINT` is `python -m app`.
5. Leave **Pre-deploy command** empty.
6. **Do not attach a volume** to `semantica-api`. It is stateless.

## 9. Configure `semantica-api` variables

Open `semantica-api`, go to **Variables**, and add exactly these:

| Variable | Value | Kind |
|---|---|---|
| `SEMANTICA_ENV` | `production` | **[FIXED]** |
| `SEMANTICA_API_KEY` | the URL-safe key from step 5 | **[GENERATE]** secret |
| `SEMANTICA_ALLOW_ANONYMOUS` | `false` | **[FIXED]** |
| `FALKORDB_HOST` | `${{falkordb.RAILWAY_PRIVATE_DOMAIN}}` | **[REFERENCE]**, resolves to `falkordb.railway.internal` |
| `FALKORDB_PORT` | `6379` | **[FIXED]** |
| `FALKORDB_PASSWORD` | `${{falkordb.FALKORDB_PASSWORD}}` | **[REFERENCE]**, so the password is stored only once |

Optional variables. Leave them unset to use the defaults:

| Variable | Default | Kind |
|---|---|---|
| `FALKORDB_GRAPH` | `zeptly_semantica` | **[FIXED]** |
| `FALKORDB_TIMEOUT_SECONDS` | `5` | **[FIXED]** |

Do **not** set `PORT`. **[RAILWAY]** Railway injects it, and the service listens on `0.0.0.0:${PORT}`.

Only for a legacy environment created before 16 October 2025, whose private network is IPv6-only, also set `SEMANTICA_BIND_HOST` = `::` **[FIXED]**. Do not set it otherwise. Railway's deploy healthcheck connects over IPv4. With `::` the service builds a socket that is explicitly dual-stack, but plain `0.0.0.0` is the tested default.

Do not add any other variables. The service reads nothing else. In particular it takes no LLM or provider keys, no database URLs and no Semantica feature flags.

## 10. Configure the healthcheck

This comes from `railway.toml` (step 8). Confirm under **Settings** → **Deploy**:

* **Healthcheck Path** is `/health`. It needs no authentication and doesn't depend on FalkorDB.
* **Healthcheck Timeout** is `60` seconds.

Railway runs this healthcheck **only while a deployment is starting**, to decide when to switch traffic to it. It does not monitor continuously. Graph connectivity is reported by `GET /ready`, which returns 200 or 503. Check `/ready` manually (step 14) and with any external monitor you add later.

`/health` was chosen as the Phase 6.25 contract. The trade-off is that a deployment with a wrong `FALKORDB_PASSWORD` still passes the healthcheck and replaces the previous deployment; `/ready` then shows 503. Always run step 14 after any variable change.

## 11. Configure restart behaviour

This also comes from `railway.toml`. Confirm under **Settings** → **Deploy** that **Restart Policy** is **On Failure** with **Max retries** set to `10`.

Do the same for `falkordb`: open **Settings** → **Deploy** → **Restart Policy** and choose **On Failure** (or **Always**).

The API does not crash when FalkorDB is unavailable. It stays up, `/ready` returns 503, and it reconnects on its own. Restarts only happen after a real process failure, such as the configuration being refused at startup (exit code 2).

## 12. Keep FalkorDB private

This is a checklist. Repeat it after every change to `falkordb`:

* [ ] **Networking** → **Public Networking** shows no domain and no TCP proxy.
* [ ] `REDIS_ARGS` contains `--requirepass`.
* [ ] The only client is `semantica-api`, which reaches FalkorDB over `*.railway.internal`.
* [ ] No other service in the project has `FALKORDB_PASSWORD` as a reference.

## 13. Expose `semantica-api` temporarily for Phase 6.25 validation, if needed

Phase 6.25C must call the API from outside Railway unless you validate from another service in the same project. If you need outside access:

1. Open `semantica-api` → **Settings** → **Networking** → **Public Networking** and click **Generate Domain**. Railway assigns `semantica-api-<suffix>.up.railway.app` **[RAILWAY]**. If Railway asks for a target port, enter the port shown in the deploy logs' `bind` event; it is normally `8080`, the `PORT` Railway injects.
2. Record the domain for the Phase 6.25C report. It is not a secret, but it is not advertised anywhere either.
3. All `/v1` routes require `X-API-Key`. `/health` and `/ready` are public and return only a fixed status.

## 14. Validate the deployment

Run these from your own machine. Put the key in an environment variable so it never appears in shell history or saved output:

```sh
read -rs SEMANTICA_API_KEY; export SEMANTICA_API_KEY     # paste key, press Enter (not echoed)
BASE="https://semantica-api-<suffix>.up.railway.app"    # your generated domain

curl -s "$BASE/health"                                   # {"status":"ok"}
curl -s "$BASE/ready"                                    # {"status":"ready"}
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/v1/workspaces/phase625-workspace-a/nodes/x"   # 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-API-Key: wrong' "$BASE/v1/workspaces/phase625-workspace-a/nodes/x"   # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: $SEMANTICA_API_KEY" "$BASE/v1/workspaces/phase625-workspace-a/nodes/x"   # 404 (authenticated, absent)
curl -s -o /dev/null -w '%{http_code}\n' "$BASE/docs"    # 404
```

In the `semantica-api` deployment logs, which are JSON lines, check the following:

| Event | Expected |
|---|---|
| `startup` | `"semantica_version": "0.6.0"`, `"env": "production"`, `"auth_required": true`, `"anonymous_allowed": false`, `"falkordb_password_set": true`, `"falkordb_host": "falkordb.railway.internal"` |
| `bind` | `"host": "0.0.0.0"`. In a legacy environment configured with `SEMANTICA_BIND_HOST=::`: `"host": "::"` and `"ipv6_only": false`. |
| `readiness` | `"ready": true` |
| `graph_connected` | present |

The full fixture, isolation, persistence and cleanup procedure is Phase 6.25C.

## 15. Later: switch `semantica-api` to private-only (Phase 6.5)

Do this when Zeptly's backend runs in, or can reach, the same Railway project's private network:

1. Set Zeptly's base URL to `http://semantica-api.railway.internal:<PORT>`.
   * This is plain HTTP inside Railway's encrypted private network, and there is no TLS on the private hop.
   * `<PORT>` is the `PORT` value shown in the deploy log `bind` event. To make it stable, you may set a `PORT` variable, for example `8080`, on `semantica-api`. This is **[FIXED]** and is the only case where setting `PORT` yourself is appropriate.
2. Check that the private hop works: `/ready` succeeds from a Zeptly container.
3. Open `semantica-api` → **Settings** → **Networking** → **Public Networking** and delete the generated domain.
4. Confirm the old public URL no longer resolves or returns Railway's "not found" page.

If Zeptly is **not** on Railway, private networking is not available. In that case, keep the public domain with API-key protection, and record that as an accepted risk in Phase 6.5. Rotate the key if it is ever exposed.

## Rollback

| Situation | Action |
|---|---|
| Bad `semantica-api` code deploy | Open **Deployments**, find the last good deployment, and use its **⋮** menu → **Rollback** (or **Redeploy**). Railway reuses that deployment's image and variables. Alternatively, revert the commit on `main` and let Railway rebuild. Builds are reproducible: the base image is pinned by digest and dependencies are hash-locked. |
| Bad variable change | Fix the variable and redeploy, or roll back to a deployment made before the change. |
| Bad FalkorDB image change | Set the image back to `falkordb/falkordb-server:v4.14.8@sha256:71afd8c7…` and redeploy. The volume is kept. |
| Corrupted or lost graph | The graph is **derived state**. Stop writes, delete the graph (or attach a fresh volume), start FalkorDB and let `/ready` go green, then **rebuild from canonical PostgreSQL** by replaying the projection. Replay is a Phase 6.5+ capability of Zeptly and is not implemented yet. Railway volume backups, where available on your plan, are an operational convenience only, never the source of truth. |
| Revert this service to a source revision | Every change is a Git commit. Pin the service's source to that commit, or roll back to the deployment built from it. |

Never delete the `falkordb` volume as a troubleshooting step unless you intend to rebuild the projection.
