# Payment Recovery System

Fault-tolerant payment reconciliation and recovery demo for Razorpay payments. The app keeps a local order/payment ledger, accepts signed Razorpay webhooks, compares local payment state with Razorpay, and recovers stale local `PENDING` payments only when Razorpay confirms the payment was captured.

## Architecture

- `backend/app/main.py` - FastAPI app, HTTP routes, webhook signature verification, dashboard serving, and optional background worker startup.
- `backend/app/database/database.py` - SQLAlchemy engine/session configuration. Set `DATABASE_URL` for your local database.
- `backend/app/models/` - SQLAlchemy tables for orders, payments, webhook events, recovery logs, recovery attempts, and queued recovery jobs.
- `backend/app/providers/razorpay.py` - Razorpay API adapter. It reads credentials from environment variables and maps Razorpay statuses into internal statuses.
- `backend/app/reconciliation/reconciliation.py` - Read-only local-vs-provider comparison.
- `backend/app/recovery/recovery.py` - Recovery logic. It updates local payment/order state only after provider status, amount, and currency match.
- `backend/app/workers/` - Polling workers for reconciliation scans and queued recovery jobs with retry/backoff.
- `backend/dashboard.html` - Same-origin demo dashboard served by `GET /dashboard`.
- `backend/checkout.html` - Legacy Razorpay checkout page. Opening/using it can create a new Razorpay test payment, so skip it unless you intentionally want that.

## Features

- Signed Razorpay webhook handling with HMAC SHA-256 verification.
- Idempotent webhook ingestion using unique `event_id` storage.
- Race-safe duplicate webhook handling around the database unique constraint.
- Local order creation with `POST /orders`.
- Read-only reconciliation with `GET /reconcile/{payment_id}`.
- Manual recovery demo with controlled failure injection.
- Recovery attempt and audit log history.
- Recovery queue with retry count, max retries, and exponential backoff.
- Optional background reconciliation/recovery worker via `ENABLE_BACKGROUND_WORKER=1`.
- Browser dashboard for loading provider state, local state, recovery history, and job status.

## Setup

Run these commands from the repository root in PowerShell:

```powershell
cd backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure local environment variables. Do not paste secrets into source files or commit them.

```powershell
$env:DATABASE_URL="postgresql://postgres:<password>@localhost:5432/payment_recovery"
$env:RAZORPAY_KEY_ID="<your_test_key_id>"
$env:RAZORPAY_KEY_SECRET="<your_test_key_secret>"
$env:RAZORPAY_WEBHOOK_SECRET="<your_webhook_secret>"
$env:ENABLE_BACKGROUND_WORKER="0"
```

Start the API:

```powershell
uvicorn app.main:app --reload
```

Open the dashboard:

```text
http://127.0.0.1:8000/dashboard
```

## Demo Steps

Use an existing Razorpay test payment ID that already exists in your account. These steps fetch existing provider state; they do not create a new Razorpay order or payment.

```powershell
$base="http://127.0.0.1:8000"
$paymentId="<existing_razorpay_payment_id>"
```

Create or reset a local `PENDING` record for that existing Razorpay payment:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "$base/test/payments/$paymentId/pending" `
  -ContentType "application/json" `
  -Body "{}"
```

Verify reconciliation detects the local/provider mismatch:

```powershell
Invoke-RestMethod "$base/reconcile/$paymentId"
```

Run one controlled failed recovery attempt:

```powershell
Invoke-RestMethod -Method Post "$base/test/recovery/$paymentId?simulate_failures=1"
```

Recover the payment locally after Razorpay confirms it is captured:

```powershell
Invoke-RestMethod -Method Post "$base/test/recovery/$paymentId?simulate_failures=0"
```

Inspect recovery history:

```powershell
Invoke-RestMethod "$base/recovery/history/$paymentId"
```

Reset local state to `PENDING`, queue a job, and check its status:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "$base/test/payments/$paymentId/pending" `
  -ContentType "application/json" `
  -Body "{}"

Invoke-RestMethod -Method Post "$base/recovery/queue/$paymentId"
Invoke-RestMethod "$base/recovery/jobs/$paymentId"
```

To let the background worker process queued jobs, restart Uvicorn with:

```powershell
$env:ENABLE_BACKGROUND_WORKER="1"
uvicorn app.main:app --reload
```

Webhook demo with a local signed payload:

```powershell
$eventId="evt_demo_001"
$body="{`"event_id`":`"$eventId`",`"event_type`":`"payment.captured`",`"payment_id`":`"$paymentId`"}"
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($env:RAZORPAY_WEBHOOK_SECRET)
$signature = [BitConverter]::ToString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).Replace("-","").ToLower()

Invoke-RestMethod -Method Post `
  -Uri "$base/webhooks/razorpay" `
  -ContentType "application/json" `
  -Headers @{"x-razorpay-signature"=$signature; "x-razorpay-event-id"=$eventId} `
  -Body $body

Invoke-RestMethod -Method Post `
  -Uri "$base/webhooks/razorpay" `
  -ContentType "application/json" `
  -Headers @{"x-razorpay-signature"=$signature; "x-razorpay-event-id"=$eventId} `
  -Body $body
```

The second webhook request should return `Event already received`.

## Safety Notes

- Do not call `POST /provider/orders` during this demo unless you intentionally want to create a Razorpay order.
- Do not use `backend/checkout.html` unless you intentionally want to open Razorpay Checkout and create a new test payment.
- Keep `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, and database passwords in environment variables only.
