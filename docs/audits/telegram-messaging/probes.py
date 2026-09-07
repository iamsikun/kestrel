import contextlib
import datetime
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from kestrel.application import Lab
from kestrel.artifacts import Artifacts
from kestrel.cli import main
from kestrel.messaging import Assistant, init_messaging
from kestrel.notifications import Channel, Destination, Exporter, FakeTransport, Gateway, Grant
from kestrel.telegram import InboundPoller, normalize_update

NOW = 1_000_000.0
ROOT = Path(tempfile.mkdtemp(prefix='kestrel-msg-audit-', dir='/private/tmp'))
results = {}

@contextlib.contextmanager
def setup(name, *, grant_seconds=86400, classification='public_synthetic'):
    base = ROOT / name
    base.mkdir()
    with Lab.initialize(base / 'lab') as lab:
        artifact = lab.store.put_bytes(b'synthetic audit bytes', producer='audit', classification=classification)
    root = base / 'messaging'
    init_messaging(root, base / 'lab')
    with Assistant(root) as assistant:
        assistant.reconcile(now=NOW)
        exporter = Exporter(assistant)
        token = (root / 'assistant-private' / 'operator.token').read_text()
        exporter.register_channel(Channel(channel_id='personal', version=1, destination=Destination(transport='fake', identity='4242')), operator_token=token, now=NOW)
        exporter.issue_grant(Grant(grant_id='notify_operator', version=1, principal='operator', channel_id='personal', allowed_conditions=[], expires_at=NOW + grant_seconds), operator_token=token, now=NOW)
        yield assistant, exporter, root, base / 'lab', artifact

with setup('daily') as (a, e, root, lab, artifact):
    initial_schedule = a.schedule()
    due = a.set_schedule(now=NOW)['next_due']
    a.reconcile(now=due+1)
    daily = [i for i in a.intents() if i['purpose']=='daily_brief']
    outcome=e.export_pending(now=due+2)
    results['daily']={'initial_schedule': initial_schedule, 'daily_count':len(daily), 'refusals': outcome['refused']}

with setup('classification', classification='restricted') as (a,e,root,lab,artifact):
    e.export_pending(now=NOW+1)
    store = Artifacts(lab / 'runtime/artifacts')
    try:
        store.invalidate(artifact['digest'], 'Synthetic restricted activity')
    finally:
        store.close()
    a.reconcile(now=NOW+2)
    intents=[i for i in a.intents() if i['purpose']=='item:evidence_correction']
    outcome=e.export_pending(now=NOW+3)
    with Gateway(root) as g:
        bodies=[g.read_envelope(i) for i in outcome['exported']]
    results['classification']={'intent_classifications':intents[0]['payload'].get('classifications'), 'detail_classifications':intents[0]['payload']['detail']['classifications'], 'exported':bodies}

with setup('expiry', grant_seconds=10) as (a,e,root,lab,artifact):
    e.export_pending(now=NOW+1)
    with Gateway(root) as g:
        g.intake(now=NOW+2)
        t=FakeTransport()
        result=g.dispatch_once(t,now=NOW+11)
        results['expired_grant']={'grant_expired_at':NOW+10,'send_time':NOW+11,'result':result}

with setup('stale') as (a,e,root,lab,artifact):
    e.export_pending(now=NOW+1)
    refresh=e.refresh_permits(now=NOW+1000)
    with Gateway(root) as g:
        g.intake(now=NOW+1000)
        result=g.dispatch_once(FakeTransport(),now=NOW+1001)
    results['stale_projection']={'stale':a.status(now=NOW+1000)['projection_stale'],'refreshed':len(refresh['issued']),'result':result}

with setup('cli') as (a,e,root,lab,artifact):
    with patch('kestrel.cli.Assistant',side_effect=PermissionError('private authority storage denied to gateway')):
        rc=main(['--messaging-root',str(root),'notify','gateway','intake'])
    e.export_pending(now=NOW+1)
    results['gateway_boundary']={'cli_exit':rc, 'published_modes':{str(p.relative_to(root)):oct(p.stat().st_mode & 0o777) for p in (root/'notification-export').rglob('*.json')}}

with setup('inbound') as (a,e,root,lab,artifact):
    with Gateway(root) as g:
        p=InboundPoller(g,None,bot_identity='12345',epoch=1,chat_id=4242,user_id=4242)
        update={'update_id':8,'message':{'message_id':1,'date':int(NOW),'chat':{'id':4242,'type':'private'},'from':{'id':4242,'is_bot':False},'text':'/stop'}}
        g._db.execute("CREATE TRIGGER audit_simulate_storage_failure BEFORE INSERT ON inbound_updates BEGIN SELECT RAISE(ABORT, 'simulated unavailable storage'); END")
        result=p.poll_once(now=NOW,updates=[update])
        results['journal_loss']={'result':result,'rows':p.journal_size(),'cursor':p.cursor()}
        forwarded=json.loads(json.dumps(update))
        forwarded['message']['forward_origin']={'type':'user','date':int(NOW),'sender_user':{'id':9999,'is_bot':False,'first_name':'Synthetic'}}
        results['forwarded_command']={'normalized':normalize_update(forwarded,chat_id=4242,user_id=4242)}

with setup('same_owner') as (a,e,root,lab,artifact):
    e.export_pending(now=NOW+1)
    with Gateway(root,owner='cli') as g1, Gateway(root,owner='cli') as g2:
        results['same_owner_leases']=[g1.acquire(now=NOW),g2.acquire(now=NOW)]

with setup('concurrent') as (a,e,root,lab,artifact):
    e.export_pending(now=NOW+1)
    with Gateway(root,owner='cli') as g1, Gateway(root,owner='cli') as g2:
        g1.intake(now=NOW+2)
        original = g1.deliveries
        calls = [0]
        second = []
        def yield_after_selection():
            rows = original()
            calls[0] += 1
            if calls[0] == 2:
                second.append(g2.dispatch_once(FakeTransport(), now=NOW+3))
            return rows
        with patch.object(g1, 'deliveries', side_effect=yield_after_selection):
            first = g1.dispatch_once(FakeTransport(), now=NOW+3)
        results['duplicate_send'] = {'first':first,'second':second,'deliveries':g1.deliveries()}

with setup('quotas') as (a,e,root,lab,artifact):
    with Gateway(root) as g:
        outcomes=[]
        with g._transaction():
            for kind in ['critical']*20 + ['automated']*15:
                outcomes.append(g._charge(kind, {}, now=NOW))
        results['automated_cap']={'allowed':sum(x is None for x in outcomes),'quota':g.quota_state(now=NOW)}

print(json.dumps(results,indent=2))
Path('/private/tmp/kestrel-messaging-audit/probe-results.json').write_text(json.dumps(results,indent=2)+'\n')
print('Synthetic fixtures:',ROOT)
