# Mattis Redbot Cogs

Custom Red-DiscordBot cog pack for Mattis CMS.

This repo is designed as the Discord interface layer for Mattis CMS. It does not replace the CMS backend. All real data/actions should go through the Mattis API so permissions, workspace isolation, billing rules, approval gates, and audit logs remain correct.

## Cog split

### Internal Mattis systems cogs
- `mattis_core` — shared API URL/token/channel config and request helper.
- `mattis_status` — quick uptime/health checks.
- `mattis_command` — internal command links and customer lookup shell.
- `mattis_support` — internal support ticket lookup/alerts shell.
- `mattis_billing` — billing/invoice/subscription lookup shell.
- `mattis_crm` — customer CRM lookup shell.
- `mattis_audit` — recent audit/security event lookup shell.
- `mattis_security` — identity-risk/security status shell.

### Customer workspace cogs
- `mattis_workspace` — customer workspace config/status shell.
- `mattis_verify` — verification/linking shell.
- `mattis_rolesync` — role sync shell.

## Install with Red Downloader

```text
[p]load downloader
[p]repo add mattis https://github.com/MattisModifications/mattis-redbot-cogs
[p]cog install mattis mattis_core mattis_status mattis_command mattis_support mattis_billing mattis_crm mattis_audit mattis_security mattis_workspace mattis_verify mattis_rolesync
[p]load mattis_core mattis_status mattis_command mattis_support mattis_billing mattis_crm mattis_audit mattis_security mattis_workspace mattis_verify mattis_rolesync
```

Replace `[p]` with your bot prefix.

## Configure core

```text
[p]mcore apiurl https://api.mattisproductions.com
[p]mcore token YOUR_PRIVATE_BOT_API_TOKEN
[p]mcore commandchannel support #cms-support
[p]mcore commandchannel billing #cms-billing
[p]mcore commandchannel audit #cms-audit
[p]mcore commandchannel security #cms-security
```

The token should be a dedicated Mattis bot/API token, not your Discord token and not a database password.

## Notes

This first package is intentionally safe. Commands can check health and call Mattis API endpoints if they exist, but they do not directly edit PostgreSQL, Stripe, Roblox, or Discord roles without API support.
