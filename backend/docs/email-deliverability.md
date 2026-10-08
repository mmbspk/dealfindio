# Email Deliverability — DNS Setup Checklist

DealFindio sends transactional email via [Resend](https://resend.com). The sending domain
(**dealfindio.com.au**) must have four DNS records in place before production email is enabled.
Without them, Gmail, Outlook, and Apple Mail will route messages to spam or reject them outright.

## Why these records matter

| Record | What it does |
|--------|-------------|
| SPF | Tells receiving servers which IPs are allowed to send mail for the domain |
| DKIM (×2) | Cryptographic signature that proves the email hasn't been tampered with |
| DMARC | Policy that tells receivers what to do when SPF/DKIM fail (report, quarantine, or reject) |

All four records are required by Gmail's bulk-sender requirements (effective Feb 2024).

---

## Step 1 — Add the Resend sending domain

1. Log in to [resend.com](https://resend.com) → **Domains** → **Add Domain**
2. Enter `dealfindio.com.au`
3. Resend will display the exact DNS values to add (DKIM keys are domain-specific — copy them from the Resend dashboard, not from this doc)

---

## Step 2 — Add DNS records

Add these records to the `dealfindio.com.au` zone (e.g. in Cloudflare or the registrar's DNS panel).

### SPF — TXT record

| Name | Type | Value |
|------|------|-------|
| `@` | TXT | `v=spf1 include:amazonses.com ~all` |

> Resend uses Amazon SES infrastructure. If an existing SPF record is present, **extend** it
> (add `include:amazonses.com`) rather than replacing it, as DNS allows only one SPF TXT per name.

### DKIM — CNAME records (×2)

Resend provides two CNAME records for DKIM rotation. Copy the exact values from the Resend dashboard.

| Name | Type | Value |
|------|------|-------|
| `resend._domainkey` | CNAME | *(copy from Resend dashboard)* |
| `resend2._domainkey` | CNAME | *(copy from Resend dashboard)* |

### DMARC — TXT record

| Name | Type | Value |
|------|------|-------|
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@dealfindio.com.au` |

Start with `p=none` (report-only) to collect data before enforcing. After 2–4 weeks with clean
reports, tighten to `p=quarantine` then `p=reject`.

---

## Step 3 — Verify in Resend

After adding the records, return to **Resend → Domains** and click **Verify**. DNS propagation
can take up to 48 hours. All four records should show a green tick before enabling production sends.

---

## Step 4 — Configure `EMAIL_FROM_ADDRESS`

Set the environment variable to the verified sending address:

```
EMAIL_FROM_ADDRESS=alerts@dealfindio.com.au
EMAIL_FROM_NAME=DealFindio
RESEND_API_KEY=re_<your_key>
```

The `unsubscribe@dealfindio.com.au` address referenced in `List-Unsubscribe` headers must accept
(or bounce gracefully) incoming mail. A catch-all or forward to an ops inbox is sufficient.

---

## Verification tools

- [MXToolbox SPF checker](https://mxtoolbox.com/spf.aspx)
- [DKIM validator](https://dkimvalidator.com)
- [DMARC analyser](https://dmarcian.com/dmarc-inspector/)
- Resend dashboard → **Domains** → domain status
