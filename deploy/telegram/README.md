# Telegram messaging deployment profile (prepared, not activated)

**Nothing in this directory is installed, enabled, or running.** These files are
review material for the T4 activation gate described in
[`docs/proposals/TELEGRAM_IMPLEMENTATION_PLAN.md`](../../docs/proposals/TELEGRAM_IMPLEMENTATION_PLAN.md)
and the operator runbook in
[`docs/MESSAGING_OPERATIONS.md`](../../docs/MESSAGING_OPERATIONS.md).

They were written on macOS and have **never been executed on any host**. No unit
was installed, no user or group was created, no file ownership was changed, and
no egress rule was applied. The measured denials they are meant to produce —
messaging acceptance conditions N-A05 and N-A12 — are therefore **unpassed**.

## What the profile is trying to achieve

Two principals on one Linux host:

| Principal | Runs | Sees | Must not reach |
|---|---|---|---|
| `kestrel-lab` | projection, disclosure, envelope export, permit refresh, inbound command processing | the lab's research databases (read only), the assistant database, the export directory (write), the gateway journal (read) | the network, the bot credential |
| `kestrel-gateway` | outbound sending, inbound long polling | the export directory (read), its own journal (write), the bot credential | controller and evidence databases, artifact objects, project workspaces, the lab operator token, the assistant database |

`PrivateNetwork=yes` on the authority units is the one restriction in this
profile that is genuinely strong and easy to verify: the projector cannot make
a network call at all. Restricting the gateway to `api.telegram.org` is harder,
because Telegram's addresses are not stable; see the egress note below.

## Files

| File | Purpose |
|---|---|
| `kestrel-messaging-projector.service` / `.timer` | reconcile source state and export approved envelopes |
| `kestrel-messaging-permits.service` / `.timer` | re-issue short-lived release permits |
| `kestrel-messaging-inbound-process.service` / `.timer` | apply journaled inbound commands |
| `kestrel-messaging-gateway.service` / `.timer` | intake and dispatch outbound envelopes |
| `kestrel-messaging-poller.service` / `.timer` | long-poll inbound updates |
| `ownership.md` | the file ownership and permission matrix these units assume |

Every unit is `Type=oneshot` driven by a timer, so there is no bespoke daemon
loop to review. The local sender lease still prevents two senders overlapping.

## Egress restriction is not solved here

`IPAddressAllow` takes addresses, and `api.telegram.org` resolves to addresses
that change. The units below deny all addresses by default and leave the
allowlist empty and commented, because writing a plausible-looking CIDR list
would be worse than admitting the gap. A real deployment needs one of:

- an egress proxy that the gateway is forced through, with the destination
  allowlist enforced there; or
- a host firewall rule maintained against the current resolved addresses; or
- a network namespace whose only route is to such a proxy.

A Python-side host check inside the adapter does **not** contain a compromised
gateway. Until one of the above is configured and measured, treat the gateway's
egress as unrestricted and scope the bot credential accordingly.

## Residual risks that this profile does not remove

- A compromised gateway can misuse its bot credential for anything that
  credential can do at Telegram. The split limits exposed research content and
  protects controller authority; it does not protect the Telegram account.
- A copied token on another host remains usable until it is rotated.
- systemd sandboxing is not a mutually-untrusted-tenant boundary.
- These units have not been tested, so any directive here may be wrong.
