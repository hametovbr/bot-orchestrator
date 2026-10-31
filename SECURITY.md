# Security policy

## Threat model

bot-orchestrator lets anyone with access to its web UI write code that runs on
the server and send messages on behalf of every managed bot. Treat access to the
UI as root access to the host.

Recommended deployment:

- Expose the web UI only on a local network or through a VPN (Tailscale,
  WireGuard), and always behind a password.
- Keep `telegram-bot-api` and `bot-runner` on the internal Docker network with no
  published ports. The local Bot API server has no authentication besides the
  bot token.
- Store the encryption key for bot tokens and secrets in an environment
  variable, never in the repository or the image.
- Restrict each bot to a list of allowed Telegram users if it downloads files or
  writes to disk.
- Use a separate Telegram account for user-bots, never your personal one.

## Reporting a vulnerability

Please do not open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/hametovbr/bot-orchestrator/security/advisories/new)
instead.
