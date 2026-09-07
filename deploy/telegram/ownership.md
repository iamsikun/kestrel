# File ownership and permission matrix (proposed, never applied)

Two service accounts and one shared group. `MESSAGING_ROOT` and `LAB_ROOT` are
external absolute paths; neither is inside the framework checkout.

Create with, for example:

```sh
# Never run on a host without explicit operator authorization.
groupadd --system kestrel-msg
useradd --system --no-create-home --shell /usr/sbin/nologin --gid kestrel-lab kestrel-lab
useradd --system --no-create-home --shell /usr/sbin/nologin --gid kestrel-gateway kestrel-gateway
usermod -aG kestrel-msg kestrel-lab
usermod -aG kestrel-msg kestrel-gateway
```

| Path | Owner | Group | Mode | kestrel-lab | kestrel-gateway |
|---|---|---|---|---|---|
| `LAB_ROOT/` | `kestrel-lab` | `kestrel-lab` | `0700` | read/write | none |
| `LAB_ROOT/operator.token` | `kestrel-lab` | `kestrel-lab` | `0600` | read | none |
| `LAB_ROOT/runtime/controller.sqlite` | `kestrel-lab` | `kestrel-lab` | `0600` | read | none |
| `LAB_ROOT/runtime/artifacts/` | `kestrel-lab` | `kestrel-lab` | `0700` | read | none |
| `MESSAGING_ROOT/` | `kestrel-lab` | `kestrel-msg` | `0750` | read/write | traverse |
| `MESSAGING_ROOT/messaging.json` | `kestrel-lab` | `kestrel-lab` | `0600` | read | none |
| `MESSAGING_ROOT/assistant-private/` | `kestrel-lab` | `kestrel-lab` | `0700` | read/write | none |
| `MESSAGING_ROOT/assistant-private/operator.token` | `kestrel-lab` | `kestrel-lab` | `0600` | read | none |
| `MESSAGING_ROOT/notification-export/` | `kestrel-lab` | `kestrel-msg` | `2750` | read/write | read |
| `MESSAGING_ROOT/notification-export/**` | `kestrel-lab` | `kestrel-msg` | `0640` | read/write | read |
| `MESSAGING_ROOT/telegram-runtime/` | `kestrel-gateway` | `kestrel-msg` | `2770` | read/write | read/write |
| `MESSAGING_ROOT/telegram-runtime/gateway.sqlite` | `kestrel-gateway` | `kestrel-msg` | `0660` | read/write | read/write |
| `MESSAGING_ROOT/telegram-secrets/` | `kestrel-gateway` | `kestrel-gateway` | `0700` | none | read |
| `MESSAGING_ROOT/telegram-secrets/bot.token` | `kestrel-gateway` | `kestrel-gateway` | `0600` | none | read |

Notes.

- The gateway journal is shared read/write because the authority side reads
  journaled inbound updates and marks them processed. Those bytes are untrusted
  input to the authority side, exactly like a worker's output.
- `read_credential` refuses any group or world readable bit, so `bot.token` must
  be `0600` and owned by `kestrel-gateway`.
- `notification-export` uses the setgid bit so envelopes and permits keep the
  shared group as the authority writes them.
- The lab's own directories are deliberately absent from every gateway unit's
  `ReadWritePaths` and `ReadOnlyPaths`, and are additionally listed under
  `InaccessiblePaths`.

## Verification that has not been performed

On the target Linux host, and under these exact identities:

1. `sudo -u kestrel-gateway cat LAB_ROOT/runtime/controller.sqlite` must fail.
2. `sudo -u kestrel-gateway sqlite3 LAB_ROOT/runtime/artifacts/evidence.sqlite` must fail.
3. `sudo -u kestrel-gateway cat LAB_ROOT/operator.token` must fail.
4. `sudo -u kestrel-gateway cat MESSAGING_ROOT/assistant-private/assistant.sqlite` must fail.
5. `sudo -u kestrel-lab cat MESSAGING_ROOT/telegram-secrets/bot.token` must fail.
6. `sudo -u kestrel-lab curl https://api.telegram.org` must fail under `PrivateNetwork=yes`.
7. Writing into `notification-export` as `kestrel-gateway` must fail.
8. Filling `notification-export` past its byte budget must pause export without
   touching any research record.

Until these are run and recorded on the target host, messaging acceptance
conditions N-A05 and N-A12D are **not passed**.
