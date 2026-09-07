# Kestrel messaging operations

Status on 2026-09-07: **T0, T1, T2, T3 and T5 are implemented and tested
offline. T4 is prepared and inert.** No bot exists, no credential has been
obtained, no message has been sent, no host service is installed, no controller
is deployed, and no permission has been expanded. Everything below that touches
Telegram is an instruction for a future authorized operator, not a record of
something that happened.

Read [`docs/proposals/TELEGRAM_IMPLEMENTATION_PLAN.md`](proposals/TELEGRAM_IMPLEMENTATION_PLAN.md)
for the design, [`BUILD_STATE.md`](../BUILD_STATE.md) for current evidence, and
[`deploy/telegram/`](../deploy/telegram/) for the deployment profile.

## 1. What works today, entirely offline

```sh
# A read-only briefing straight from verified records. Opens no writable store.
kestrel --lab /external/lab brief --format markdown
kestrel --lab /external/lab brief --since 2026-09-01T00:00:00+00:00 --format json

# Create the external messaging state. This installs no service and provisions
# no credential. The messaging root must be outside both the lab and this repo.
kestrel --messaging-root /external/messaging --lab /external/lab notify init \
    --timezone America/Chicago --brief-time 08:00

# Project source state into the durable inbox and inspect it.
kestrel --messaging-root /external/messaging notify reconcile --once
kestrel --messaging-root /external/messaging inbox list
kestrel --messaging-root /external/messaging inbox show M3-r1
kestrel --messaging-root /external/messaging notify status

# Attention, watches, milestones and the schedule are local preferences.
kestrel --messaging-root /external/messaging notify ack M3-r1
kestrel --messaging-root /external/messaging notify snooze M3-r1 tomorrow
kestrel --messaging-root /external/messaging notify watch campaign:CAMPAIGN_ID
kestrel --messaging-root /external/messaging notify milestone add --id gate \
    --definition /external/gate.json
kestrel --messaging-root /external/messaging notify schedule set --time 08:00
kestrel --messaging-root /external/messaging notify pause
kestrel --messaging-root /external/messaging notify resume

# Delivery, with an offline transport only.
kestrel --messaging-root /external/messaging notify channel register --id personal \
    --transport fake --identity 4242 \
    --operator-token-file /external/messaging/assistant-private/operator.token
kestrel --messaging-root /external/messaging notify grant issue --id notify_operator \
    --channel personal \
    --operator-token-file /external/messaging/assistant-private/operator.token
kestrel --messaging-root /external/messaging notify export --once
kestrel --messaging-root /external/messaging notify gateway intake
kestrel --messaging-root /external/messaging notify gateway dispatch --transport fake
kestrel --messaging-root /external/messaging notify delivery list

# Documentation record; makes no request.
kestrel --messaging-root /external/messaging notify telegram assumptions
```

Every command prints an `authority` field saying whether it changes attention
only, changes local state, needs an operator grant, or would contact a network.
`--transport none` and `--transport fake` never open a socket. Preview and
inspection commands never contact Telegram.

A milestone definition is a typed document, not a rule to execute:

```json
{
  "definition_version": "0.1",
  "label": "Numerical comparison complete",
  "predicates": [
    {
      "campaign_id": "CAMPAIGN_ID",
      "terminal_state": "COMPLETE",
      "required_protocol": "valid",
      "allowed_findings": ["supported_in_scope", "not_supported"],
      "required_assurance": ["independently_recomputed"]
    }
  ]
}
```

There is deliberately no expression, SQL fragment, or "queue is empty"
predicate. An empty queue never means a project is finished.

## 2. The activation gate

Every operation that would reach `api.telegram.org` fails closed unless
`KESTREL_TELEGRAM_ACTIVATED=1` is set in that process's environment. Nothing in
the codebase sets it, no test sets it, and it is not a development switch: the
whole offline release works without it.

Setting it is the operator's statement that live Telegram activity is
authorized. It does not by itself authorize deployment, controller changes,
live model providers, or any expansion of research permissions.

## 3. Inputs to collect before the first authorized test

The plan lists these as preferences to settle before activation. None of them
block the offline work above.

- IANA timezone, briefing time, and quiet-hour policy, including which condition
  classes — if any — may interrupt quiet hours.
- Which campaigns and milestones to watch.
- Disclosure profile: the channel's classification ceiling, and whether a
  metadata-only fallback is permitted at all (it is off by default).
- Grant lifetime and the daily caps to start with.
- Absolute external paths for `LAB_ROOT` and `MESSAGING_ROOT`.
- The deployment host and OS identity, if a service is wanted at all.

## 4. Operator steps for the first authorized synthetic Telegram test

Do not begin until an authorized operator has decided to run it. Use public
synthetic content only. Expect to send exactly one message.

1. **Create a dedicated bot.** Talk to `@BotFather` in your own Telegram client,
   create a bot for this assistant only, and copy its token. Use one bot per
   lab so another installation cannot consume its updates.
2. **Place the credential.** Write the token to
   `MESSAGING_ROOT/telegram-secrets/bot.token`, then `chmod 600` it. The adapter
   refuses any group or world readable bit. Never pass the token as a command
   argument, never paste it into a chat, and never put it in a shell history
   file. Prefer `install -m600 /dev/null <path>` then an editor.
3. **Confirm the offline state is healthy.** Run `notify reconcile --once` and
   `notify status`. `projection_stale` must be false; export is disabled while
   projection is stale.
4. **Validate identity and webhook state.** With the activation variable set for
   this one command:
   ```sh
   KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
       notify telegram begin --credential-file /external/messaging/telegram-secrets/bot.token
   ```
   This calls `getMe` and `getWebhookInfo` only. If another integration owns the
   bot's webhook it stops with a conflict; do not delete that webhook and do not
   discard its pending updates — resolve it with its owner.
5. **Pair the chat.** Open the printed `start_link` in your own Telegram client
   and press start. Then:
   ```sh
   KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
       notify telegram poll --credential-file /external/messaging/telegram-secrets/bot.token
   ```
   Only a private chat, a non-bot sender, and the exact unconsumed nonce are
   accepted. Anything else is recorded as a bounded rejection reason. The nonce
   expires in ten minutes and only its hash is stored.
6. **Confirm locally.** Review the printed candidate identity, then bind it:
   ```sh
   kestrel --messaging-root /external/messaging notify telegram confirm \
       --operator-token-file /external/messaging/assistant-private/operator.token
   ```
   This needs no network. Do not adopt whoever happens to message the bot: the
   nonce identifies a chat, the local confirmation is what approves it.
7. **Send exactly one synthetic message.**
   ```sh
   kestrel --messaging-root /external/messaging notify export --once
   KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
       notify gateway intake
   KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
       notify gateway dispatch --transport telegram \
       --credential-file /external/messaging/telegram-secrets/bot.token
   ```
8. **Record two separate facts.** The delivery journal records that the API
   accepted the request. Whether the message appeared on your device is a
   separate manual observation. Write down both, plus the date, the bot id and
   the chat id. A human sighting is integration evidence for that message; it is
   not a general delivery guarantee, and API acceptance is not delivery,
   reading, review, or approval.
9. **Stop there.** Do not enable a timer, do not enable inbound polling, and do
   not widen the grant in the same session.

Rollback at any point: `notify pause` stops automated delivery immediately;
`notify grant revoke` withdraws every release permit within the declared
60-second bound and blocks pending sends. Neither can recall a message the API
already accepted, and neither revokes the bot token at Telegram — rotate the
token with BotFather for that.

## 5. Enabling inbound commands, later and separately

Inbound is implemented and tested offline but is a distinct authorization.
Once enabled, the vocabulary is exactly `/help`, `/status`, `/brief`, `/inbox`,
`/ack`, `/snooze`, `/stop`, `/resume`. There is no approve, run, cancel, budget
or policy command anywhere in the dispatch table.

```sh
KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
    notify inbound poll --credential-file /external/messaging/telegram-secrets/bot.token
kestrel --messaging-root /external/messaging notify inbound process --once
kestrel --messaging-root /external/messaging notify inbound journal
```

`poll` contacts Telegram; `process` and `journal` do not. Treat the gateway's
inbound journal as untrusted: its compromise can affect attention state and
forge transport-level acknowledgement within these limits, but it cannot obtain
scientific review status or any research execution authority.

## 6. Credential rotation

Pause first, reconcile any ambiguous attempt, then:

```sh
kestrel --messaging-root /external/messaging notify pause
KESTREL_TELEGRAM_ACTIVATED=1 kestrel --messaging-root /external/messaging \
    notify telegram rotate --credential-file /external/messaging/telegram-secrets/bot.token
kestrel --messaging-root /external/messaging notify resume
```

Rotation confirms the new credential still identifies the enrolled bot, or tells
you re-enrollment is required. It cannot resolve an already uncertain send, and
a copied token on another host stays usable until it is rotated at BotFather.

## 7. Host deployment

Everything in [`deploy/telegram/`](../deploy/telegram/) is review material. It
has never been installed or executed, on this machine or any other. Installing
it requires its own explicit operator authorization and an independent review,
and the measured denials in `deploy/telegram/ownership.md` must actually be run
on the target Linux host before the corresponding messaging acceptance
conditions can be called passed.

The one restriction in that profile that is genuinely strong is
`PrivateNetwork=yes` on the authority units: the projector cannot make a network
call at all. Restricting the gateway's egress to Telegram is **not** solved
there, because the service's addresses are not stable; see the egress note in
`deploy/telegram/README.md`.

## 8. Separately invoked live integration test

`tests/test_telegram_live.py` exists and is skipped by default. It is marked
`live`, it is excluded from the documented core command, and it additionally
refuses to run unless every one of these is supplied:

```sh
KESTREL_RUN_LIVE_TELEGRAM=1 \
KESTREL_TELEGRAM_ACTIVATED=1 \
KESTREL_TELEGRAM_CREDENTIAL=/external/messaging/telegram-secrets/bot.token \
KESTREL_TELEGRAM_CHAT_ID=<numeric chat id> \
.venv/bin/python -m pytest tests/test_telegram_live.py -m live -q \
    --allow-hosts=api.telegram.org
```

It sends at most one message with public synthetic content. It has **never been
run**, and no token or chat identifier appears in any recorded JUnit file or
fixture in this repository. A skip here is an unsatisfied gate, not a pass.

## 9. What remains explicitly unverified

| Condition | Why it is not passed |
|---|---|
| N-A05 | Needs the Linux deployment profile installed and the cross-identity read and write denials actually measured |
| N-A12 | Needs enforced egress restriction on the deployed host; a Python-side host check is not containment |
| N-A19 (live half) | Fake-transport acceptance is not evidence about a real provider or device |
| N-A20 | Deferred with the N6 literature increment |
| A28, A30, A31, A43 | Pre-existing first-pilot blockers, unaffected by messaging |

Passing the messaging tests clears none of these, and clears no unrelated
release gate.
