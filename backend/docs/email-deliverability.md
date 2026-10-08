# Email Deliverability — DNS Setup Checklist

DealFindio sends transactional email via [Resend](https://resend.com). The sending domain
(**dealfindio.com.au**) must have SPF, DKIM, and DMARC DNS records in place before
production email is enabled. Without them, Gmail, Outlook, and Apple Mail will route
messages to spam or reject them outright.

> **All SPF and DKIM values must be copied from the Resend dashboard — they are unique
> to your account and domain. Do not use values from this document or other guides.**

---

## Why these records matter

| Record | What it does |
|--------|-------------|
| SPF | Authorises which mail servers may send on behalf of the domain |
| DKIM | Cryptographic signature proving the email content was not tampered with |
| DMARC | Tells receivers what to do when SPF/DKIM fail; enables aggregate reporting |

All three are required by Gmail's bulk-sender rules (effective February 2024) and by
Microsoft/Outlook for anything above low-volume sending.

---

## Step 1 — Add the sending domain in Resend

1. Log in to [resend.com](https://resend.com) → **Domains** → **Add Domain**
2. Enter `dealfindio.com.au`
3. Resend will display the exact record names and values to add.
   Copy these directly — do not modify them.

---

## Step 2 — Add DNS records

Add the records Resend provides to the `dealfindio.com.au` DNS zone
(via Cloudflare or your domain registrar's control panel).

Resend provides:

- A TXT record for **SPF**. If an existing SPF record is present, extend it — DNS allows
  only one SPF TXT record per name.
- One or more CNAME records for **DKIM** signing. The names and values are unique to your
  account; do not reuse records from other domains or documentation.

You write the DMARC record yourself:

| Name | Type | Value |
|------|------|-------|
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@dealfindio.com.au` |

Start with `p=none` (report-only) to collect alignment data. After 2-4 weeks with clean
reports, progress to `p=quarantine` then `p=reject`.

---

## Step 3 — Verify in Resend

After adding the records, return to **Resend → Domains** and click **Verify**. DNS
propagation can take up to 48 hours. All records should show a green tick before sending
production emails.

---

## Step 4 — Set environment variables

```env
EMAIL_FROM_ADDRESS=alerts@dealfindio.com.au
EMAIL_FROM_NAME=DealFindio
RESEND_API_KEY=re_<your_key>
```

The `unsubscribe@dealfindio.com.au` address referenced in `List-Unsubscribe` headers must
accept (or bounce gracefully) incoming mail. A catch-all forward to an ops inbox is sufficient.

---

## Step 5 — One-click unsubscribe endpoint

The `List-Unsubscribe-Post: List-Unsubscribe=One-Click` header (RFC 8058) requires a POST
endpoint at the unsubscribe URL. This is implemented at:

```http
POST /api/v1/notifications/unsubscribe?token=<signed-token>
```

Gmail POSTs `List-Unsubscribe=One-Click` form data to this URL when the user clicks the
unsubscribe button in the message header. The `GET` variant shows a human-facing
confirmation page (non-mutating, to prevent automated link pre-fetch from unsubscribing users).

---

## Step 6 — 90 KB size guard (observability)

`email_sender.py` logs a `WARNING` when rendered HTML exceeds 90 KB before sending.
This is **monitoring only** — it does not prevent the email from being sent or reduce its
size. Use it to identify templates approaching Gmail's 102 KB clipping threshold so they
can be optimised before users see truncated emails.

---

## Verification tools

- [MXToolbox SPF checker](https://mxtoolbox.com/spf.aspx)
- [DKIM validator](https://dkimvalidator.com)
- [DMARC analyser](https://dmarcian.com/dmarc-inspector/)
- Resend dashboard → **Domains** → domain status (authoritative)
