# Stockpilot — Inventory & Replenishment Management

A full-stack student project for retailers and warehouses: React UI, Flask API, SQLite database, and a DevOps deployment toolkit. All example inventory is fictional. Currency is INR and movement timestamps are UTC.

## Quick start on Windows

Install Python 3.12+ and Node.js 22, extract the project, and open PowerShell in the `stockpilot` directory:

```powershell
.\start.ps1
```

The script installs dependencies, builds React, asks for a demo password, and starts the application at **http://localhost:5000**. Internet access is required for the first installation. If PowerShell blocks scripts, use the manual commands below instead of changing system-wide policy.

Demo usernames: `admin`, `manager`, `storekeeper`, `procurement`. All use the password you choose at first initialization. Passwords are stored as hashes. Demo seeding happens only when there are no users; changing DEMO_PASSWORD later does not change existing passwords.

## Manual setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
cd frontend
npm.cmd install
npm.cmd run build
cd ..
$env:SECRET_KEY = 'set-a-long-random-secret-here'
$env:DEMO_PASSWORD = 'choose-your-own-12-character-password'
.\.venv\Scripts\python.exe backend/app.py
```

On Linux/macOS use `.venv/bin/python`, `npm`, and `export NAME=value` equivalents. A stable SECRET_KEY preserves sessions across restarts. Without it, a random process-local key is used. Data persists in `backend/inventory.db`. For UI development, run `npm run dev` in `frontend` while Flask runs on port 5000; Vite proxies `/api` calls.

## Features

- Overview: inventory value, units on hand, low/out-of-stock alerts, excess-stock counts, and stock-health bars calculated from persisted data.
- Inventory: SKU search, location/status filtering, product creation, current quantities and recorded unit costs.
- Stock movements: receipts, issues, customer returns, and signed adjustments with required reference/reason, actor, UTC timestamp, and resulting balance.
- Replenishment: configurable daily demand, lead time, safety stock, review period, and suggested order quantities.
- Reports: date-filtered audit ledger and CSV export. The export explicitly covers all locations for the selected dates.
- Team: administrator creates accounts with one of four roles. Backend permissions enforce access independently of the UI.

| Action | Storekeeper | Inventory Manager | Procurement Officer | Administrator |
|---|---|---|---|---|
| Read inventory / reports | Yes | Yes | Yes | Yes |
| Record movements | Yes | Yes | No | Yes |
| Create products | No | Yes | No | Yes |
| Edit reorder rules | No | Yes | Yes | Yes |
| List / create users | No | No | No | Yes |

## Inventory rules

`reorder point = ceil(daily demand × lead time + safety stock)`

`target stock = ceil(daily demand × (lead time + review period) + safety stock)`

`suggested order = max(0, target stock − quantity on hand)`

Zero quantity is **Out of stock**; quantity at or below reorder point is **Low stock**. Above twice the target (with a minimum target of one) is **Excess stock**; the rest is **Healthy**. Excess is a quantity-based heuristic, not an aging or turnover forecast. Demand is configured manually rather than learned automatically. Suggestions do not deduct inbound purchase orders because purchase-order management is outside this version.

Receipt and customer return add positive units. Issue subtracts positive units. Adjustment applies a signed delta, not an absolute count. A correction from 20 to 17 therefore uses −3. The backend uses an immediate SQLite transaction for balance check, update, and ledger insert, preventing concurrent issues from overselling. There is no movement deletion: use a compensating adjustment with a reason.

Each SKU identifies one stock record in one location. To hold the same physical product in multiple locations, use distinct location-specific SKUs in this version. Transfers, supplier returns, reservations, tax calculations, automated ordering, password recovery, and account disabling are not implemented.

## Architecture and source map

React browser UI → same-origin Flask JSON API → SQLite (users, products, movements).

- `frontend/src/main.jsx`: screens, forms, client state and API calls.
- `frontend/src/style.css`: responsive dashboard and login styling.
- `backend/app.py`: authentication, permissions, validation, transactions, CSV and static hosting.
- `backend/test_app.py`: Pytest regression tests for balances, rollback, permissions, CSRF, rules, export, and users.
- `Dockerfile`, `compose.yaml`: production frontend build, Waitress server, persisted database, optional monitoring.
- `.github/workflows/ci.yml`: backend tests, frontend build, Docker build on push/PR.
- `ops/`: Kubernetes, Terraform, Ansible, Prometheus and Grafana examples.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/login` | Username/password login; returns CSRF token |
| GET | `/api/me` | Current user and CSRF token |
| POST | `/api/logout` | End session |
| GET / POST | `/api/products` | List / create products |
| PUT | `/api/products/{id}/rules` | Configure reorder rules |
| GET / POST | `/api/movements` | List / record movements |
| GET | `/api/reports.csv?start=YYYY-MM-DD&end=YYYY-MM-DD` | Export all locations |
| GET / POST | `/api/users` | Administrator list / create users |
| GET | `/api/health` | Database health check |
| GET | `/metrics` | Prometheus Python runtime/process metrics |

Authenticated mutations require the session cookie and `X-CSRF-Token`. API errors return an `error` field with 400, 401, 403, 404 or 409 as appropriate. Product and user names are length-limited; quantities are integer units. CSV neutralizes formula-leading string cells.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend -q
cd frontend
npm.cmd run build
```

**Verification in the creation environment:** Python syntax, JSON structure, SQLite schema and constraints were checked offline. Package registry DNS resolution failed, so dependency installation, Pytest execution, React compilation, browser testing, and container/deployment tests could not be completed here. The CI workflow runs these checks after you push to GitHub with normal network access. Do not treat the deployment examples as already deployed or production-certified.

## Docker Compose

Copy `.env.example` to `.env` and replace all three passwords/secrets, then:

```sh
docker compose up --build -d
# Optional monitoring:
docker compose --profile monitoring up --build -d
```

App: http://localhost:5000 · Prometheus: http://localhost:9090 · Grafana: http://localhost:3000 (username `admin`, password from GRAFANA_PASSWORD). The Grafana datasource and application-up/memory dashboard are provisioned automatically. Prometheus exposes runtime metrics, not business inventory metrics. Ports bind to loopback. `docker compose down` retains data; `down -v` deletes volumes and should only be used when deliberately resetting your data.

## Other deployment examples

- **Kubernetes:** build/tag `stockpilot:1.0.0` and load it into your local cluster (or use your registry image). Create Secret `stockpilot-secrets` with SECRET_KEY and DEMO_PASSWORD, then apply `ops/kubernetes.yaml`. A default StorageClass is required. Access using `kubectl port-forward service/stockpilot 5000:80`. Keep one replica and Recreate strategy for this SQLite design.
- **Terraform:** `ops/terraform` provisions a local Docker image, container, and named volume; it does not provision a cloud account. With Docker running, run `terraform init`, set sensitive variables through your environment or secret tooling, then `terraform plan` and `terraform apply`. Terraform state contains secrets; protect it and never commit it. Do not run it alongside Compose on the same port.
- **Ansible:** intended for a Docker/Compose-ready Linux host with rsync. Install collections from `ops/ansible/requirements.yml`; define an `inventory_servers` inventory group and provide `stockpilot_secret`, `stockpilot_password`, and `grafana_password` using Ansible Vault. Run from `ops/ansible`. The playbook copies this source and starts Compose; it does not install Docker or configure a public domain.
- **Git/GitHub:** initialize Git, add this source, commit, add your own repository remote, and push. No repository or cloud account was created automatically.

## Before exposing to real users

This is a functional project implementation intended for local demonstration and further development. Put it behind HTTPS, set COOKIE_SECURE=1, use a stable secret, remove demo accounts/data through a controlled migration, add login rate limiting, account/password lifecycle management and operational backups. Keep `/metrics` private at your proxy. Back up SQLite with its online backup API or stop writes first; copying only the main file while WAL is active can miss data. SQLite is suitable for a single-instance small deployment; migrate to a server database for horizontally scaled production. No live deployment was performed.
