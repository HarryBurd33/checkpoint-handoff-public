import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/task-checkpoint/scripts'))
import checkpoint as cp
import planner
import router
import router_policy as policy
import telemetry
import transfer
import test_monitor as fixtures_monitor
import test_router_runtime as fixtures_router


class PlanTests(unittest.TestCase):
    g=fixtures_router.RouterRuntimeTests.g
    prepare=fixtures_router.RouterRuntimeTests.prepare
    def setUp(self):
        fixtures_router.RouterRuntimeTests.setUp(self)
        contract=copy.deepcopy(self.task['routing']);contract['escalation_reason']='Escalate to a higher qualified assignment on failed validation, within retry budget'
        package={'objective':'Return exactly42','permitted_files':[],'permitted_actions':['return-result'],
                 'capabilities':[],'assignment':{'model':'deterministic-fixture','effort':'medium','reason':'Test-only qualified adapter'},
                 'checkpoint_boundary':'Exact result acceptance passes','contract':contract}
        self.task['routing']['work_packages']=[package]
        self.prepare()
    def test_automatic_boundary_acceptance_and_dedup(self):
        planner.approve(self.root,self.source,self.config_path,'fixture package','current test')
        result=planner.boundary(self.root,self.source,self.config_path,self.signals,True)
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(planner.boundary(self.root,self.source,self.config_path,self.signals,True)['action'],'complete')
        self.assertEqual(len(router.state(self.root)['history']),1)
    def test_dependencies_and_uncertainty(self):
        p=copy.deepcopy(self.task['routing']['work_packages'][0]);p['contract']['id']='second';p['contract']['dependencies']=[{'id':'one','status':'pending'}]
        self.task['routing']['work_packages'].append(p)
        choice=planner.select(self.task,self.config,self.signals,router.state(self.root))
        self.assertEqual(choice['task']['routing']['id'],'one')
        self.assertEqual(planner.select(self.task,self.config,self.signals,{'active':{'status':'uncertain'},'history':[]})['action'],'pause')
        self.task['routing']['work_packages'][0]['contract']['dependencies']=[{'id':'second','status':'pending'}]
        with self.assertRaisesRegex(cp.Fault,'cyclic'):planner.validate(self.task['routing']['work_packages'])
    def test_accepted_id_cannot_change_in_plan(self):
        planner.approve(self.root,self.source,self.config_path,'fixture','test')
        planner.boundary(self.root,self.source,self.config_path,self.signals,True)
        self.task['routing']['work_packages'][0]['objective']='Different objective'
        with self.assertRaisesRegex(cp.Fault,'changed after execution'):
            planner.select(self.task,self.config,self.signals,router.state(self.root))

    def test_source_change_requires_review(self):
        planner.approve(self.root,self.source,self.config_path,'fixture','test')
        (self.root/'work.txt').write_text('changed')
        with self.assertRaisesRegex(cp.Fault,'fresh review'):planner.boundary(self.root,self.source,self.config_path,self.signals,True)
    def test_source_change_between_approval_and_authorization_is_denied(self):
        planner.approve(self.root,self.source,self.config_path,'fixture','test')
        original=cp.checkpoint
        def raced(*args,**kwargs):
            (self.root/'work.txt').write_text('concurrent editor')
            return original(*args,**kwargs)
        with mock.patch.object(cp,'checkpoint',side_effect=raced):
            with self.assertRaisesRegex(cp.Fault,'Source changed after plan approval'):
                planner.boundary(self.root,self.source,self.config_path,self.signals,True)
        self.assertIsNone(router.state(self.root)['active'])

    def test_same_model_lower_effort_can_be_selected(self):
        low=copy.deepcopy(self.config['workers'][0]);low.update(id='same-low',effort='low',estimated_relative_cost=1)
        self.config['workers'][0].update(estimated_relative_cost=2)
        self.config['workers'].append(low)
        choice=planner.select(self.task,self.config,self.signals,router.state(self.root))
        self.assertEqual(choice['assignment']['effort'],'low')
        self.assertIsNone(choice['assignment']['usage_savings'])
    def test_raw_twenty_percent_persists_stop(self):
        signals={'records':[{'kind':'codex_plan','source':'synthetic','observed_at':cp.now(),'remaining':20,'unit':'percent_remaining'}]}
        result=router.dispatch(self.root,self.source,self.config_path,signals,execute=True)
        self.assertTrue(result['normal_work_must_stop'])
        self.assertEqual(cp.read(self.root/'.checkpoint/automatic-stop.json')['checkpoint_status'],'verified')
        # A different model on the shared allowance cannot bypass the durable stop.
        with self.assertRaises(cp.Fault):router.dispatch(self.root,self.source,self.config_path,self.signals,execute=True)


class BundleTests(unittest.TestCase):
    def setUp(self):fixtures_monitor.MonitorTests.setUp(self)
    def package(self):
        self.archive=Path(self.tmp.name)/'bundle.zip'
        return transfer.package(self.root,self.archive)
    def test_roundtrip_is_quarantined_and_reproducible(self):
        meta=self.package();first=self.archive.read_bytes()
        self.assertEqual(first,transfer.zip_bytes({i.filename:zipfile.ZipFile(self.archive).read(i) for i in zipfile.ZipFile(self.archive).infolist()}))
        store=self.root/'.checkpoint';before=cp.read(store/'latest.json')
        receipt=transfer.quarantine(self.root,self.archive,meta['archive_sha256'],self.binding['project_id'],self.binding['task_id'],meta['source_revision'],store/'incoming'/meta['checkpoint_id'])
        self.assertFalse(receipt['worker_started']);self.assertFalse(receipt['owner_acquired'])
        self.assertEqual(cp.read(store/'latest.json'),before)
        self.assertNotIn('config.json',zipfile.ZipFile(self.archive).namelist())
        again=self.package();self.assertEqual(meta['archive_sha256'],again['archive_sha256'])
    def test_identity_revision_corruption_and_traversal_refused(self):
        meta=self.package()
        for project,task,revision in [('wrong',self.binding['task_id'],meta['source_revision']),(self.binding['project_id'],'wrong',meta['source_revision']),(self.binding['project_id'],self.binding['task_id'],'wrong')]:
            with self.assertRaises(cp.Fault):transfer.verify(self.archive,project,task,revision)
        with zipfile.ZipFile(self.archive,'a') as z:z.writestr('../escape',b'no')
        with self.assertRaises(cp.Fault):transfer.verify(self.archive,self.binding['project_id'],self.binding['task_id'],meta['source_revision'])
    def test_previous_valid_generation_preserved(self):
        first=cp.latest(self.store)[0]['id']
        (self.root/'work.txt').write_text('beta')
        cp.checkpoint(self.root,self.store/'input.json')
        meta=self.package()
        with zipfile.ZipFile(self.archive) as z:
            self.assertIn('snapshots/'+first+'/state.json',z.namelist())
    def test_offline_preserves_local_checkpoint(self):
        before=cp.read(self.store/'latest.json')
        with mock.patch.object(transfer,'gh',side_effect=cp.Fault('offline')):
            with self.assertRaises(cp.Fault):transfer.publish(self.root,'example/fixture')
        self.assertEqual(cp.read(self.store/'latest.json'),before)
    def test_public_repository_transport_refused(self):
        def response(args):
            return json.dumps({'full_name':'example/fixture','private':False,'permissions':{'push':True}} if args[-1]!='user' else {'login':'example'}).encode()
        with mock.patch.object(transfer,'gh',side_effect=response):
            with self.assertRaisesRegex(cp.Fault,'Private'):transfer.private_repo('example/fixture')

    def test_interrupted_upload_retries_without_duplicate_asset(self):
        uploads=[];remote={};release={}
        def backend(args):
            if args==['api','user']:return b'{"login":"example"}'
            if args==['api','repos/example/fixture']:return b'{"full_name":"example/fixture","private":true,"permissions":{"push":true}}'
            if args[:2]==['api','repos/example/fixture/releases?per_page=100']:return json.dumps([release] if release else []).encode()
            if args[0]=='api' and '/commits/' in args[1]:return b'{}'
            if args[:2]==['release','create']:
                release.update(tag_name=args[2],assets=[]);return b''
            if args[0]=='api' and '/releases/tags/' in args[1]:return json.dumps(release).encode()
            if args[:2]==['release','upload']:
                path=Path(args[3]);uploads.append(path.name);remote[path.name]=path.read_bytes()
                release['assets']=[{'name':path.name,'state':'uploaded'}]
                if len(uploads)==1:raise cp.Fault('connection lost after upload side effect')
                return b''
            if args[:2]==['release','download']:
                dest=Path(args[args.index('--dir')+1]);asset=args[args.index('--pattern')+1]
                (dest/asset).write_bytes(remote[asset]);return b''
            raise AssertionError(args)
        with mock.patch.object(transfer,'gh',side_effect=backend):
            with self.assertRaises(cp.Fault):transfer.publish(self.root,'example/fixture')
            result=transfer.publish(self.root,'example/fixture')
            self.assertEqual(result['status'],'verified');self.assertEqual(len(uploads),1)
            self.assertTrue(transfer.publish(self.root,'example/fixture')['deduplicated'])
        self.assertEqual(cp.latest(self.store)[0]['id'],self.initial['id'])
    def test_reviewed_adoption_keeps_stop_and_does_not_import_permission(self):
        cp.atomic(self.store/'automatic-stop.json',cp.encoded({'low_kinds':['codex_plan'],'checkpoint_status':'verified','checkpoint_id':self.initial['id']}))
        meta=self.package();ident=meta['checkpoint_id']
        transfer.quarantine(self.root,self.archive,meta['archive_sha256'],self.binding['project_id'],self.binding['task_id'],meta['source_revision'],self.store/'incoming'/ident)
        _,report=transfer.inspect_incoming(self.root,ident)
        cp.atomic(self.store/'plan-approval.json',cp.encoded({'stale':'not-imported'}))
        review={'checkpoint_id':ident,'current_fingerprint':report['current_fingerprint'],'observed_at':cp.now(),
                'authorization':{'confirmed':True,'scope':'adopt fixture data only','source':'current synthetic operator'},
                'destination_reconciliation':'Same verified source revision and snapshot; no dirty files or jobs'}
        cp.atomic(self.store/'adopt-review.json',cp.encoded(review))
        result=transfer.adopt(self.root,ident,self.store/'adopt-review.json')
        self.assertFalse(result['owner_acquired']);self.assertFalse(result['worker_started'])
        self.assertFalse((self.store/'plan-approval.json').exists())
        self.assertTrue((self.store/'automatic-stop.json').exists())
        self.assertFalse((self.store/'owner.json').exists())
    def test_tampered_quarantine_extraction_is_never_promoted(self):
        meta=self.package();ident=meta['checkpoint_id']
        incoming=self.store/'incoming'/ident
        transfer.quarantine(self.root,self.archive,meta['archive_sha256'],self.binding['project_id'],self.binding['task_id'],meta['source_revision'],incoming)
        tampered=cp.read(incoming/'semantic-input.json');tampered['objective']='fabricated accepted work'
        cp.atomic(incoming/'semantic-input.json',cp.encoded(tampered))
        _,report=transfer.inspect_incoming(self.root,ident)
        review={'checkpoint_id':ident,'current_fingerprint':report['current_fingerprint'],'observed_at':cp.now(),
                'authorization':{'confirmed':True,'scope':'fixture','source':'test'},'destination_reconciliation':'same source'}
        cp.atomic(self.store/'adopt-review.json',cp.encoded(review))
        transfer.adopt(self.root,ident,self.store/'adopt-review.json')
        self.assertEqual(cp.read(self.store/'input.json')['objective'],self.task['objective'])
    def test_interrupted_adoption_retries_verified_incoming_data(self):
        original_input=cp.read(self.store/'input.json')
        incoming_input=copy.deepcopy(original_input);incoming_input['objective']='Incoming beta objective'
        cp.atomic(self.store/'input.json',cp.encoded(incoming_input));meta=self.package()
        cp.atomic(self.store/'input.json',cp.encoded(original_input))
        ident=meta['checkpoint_id'];incoming=self.store/'incoming'/ident
        transfer.quarantine(self.root,self.archive,meta['archive_sha256'],self.binding['project_id'],self.binding['task_id'],meta['source_revision'],incoming)
        _,report=transfer.inspect_incoming(self.root,ident)
        review={'checkpoint_id':ident,'current_fingerprint':report['current_fingerprint'],'observed_at':cp.now(),
                'authorization':{'confirmed':True,'scope':'fixture','source':'test'},'destination_reconciliation':'Retain original input and adopt beta'}
        cp.atomic(self.store/'adopt-review.json',cp.encoded(review))
        atomic=cp.atomic
        def interrupted(path,data):
            result=atomic(path,data)
            if path==self.store/'input.json':raise RuntimeError('injected power loss after incoming input install')
            return result
        with mock.patch.object(cp,'atomic',side_effect=interrupted):
            with self.assertRaises(RuntimeError):transfer.adopt(self.root,ident,self.store/'adopt-review.json')
        self.assertEqual(cp.read(self.store/'latest.json')['id'],self.initial['id'])
        result=transfer.adopt(self.root,ident,self.store/'adopt-review.json')
        self.assertEqual(result['adopted'],ident)
        self.assertEqual(cp.read(self.store/'input.json')['objective'],'Incoming beta objective')
        self.assertEqual(cp.read(self.store/'adoption-retired'/ident/'input.json'),original_input)

    def test_adoption_refuses_competing_owner(self):
        meta=self.package();ident=meta['checkpoint_id']
        transfer.quarantine(self.root,self.archive,meta['archive_sha256'],self.binding['project_id'],self.binding['task_id'],meta['source_revision'],self.store/'incoming'/ident)
        _,report=transfer.inspect_incoming(self.root,ident)
        review={'checkpoint_id':ident,'current_fingerprint':report['current_fingerprint'],'observed_at':cp.now(),
                'authorization':{'confirmed':True,'scope':'fixture','source':'test'},'destination_reconciliation':'fixture'}
        cp.atomic(self.store/'adopt-review.json',cp.encoded(review))
        cp.Ownership(self.root,self.binding,self.store).acquire('other-device')
        with self.assertRaisesRegex(cp.Fault,'still owned'):transfer.adopt(self.root,ident,self.store/'adopt-review.json')
        self.assertEqual(cp.latest(self.store)[0]['id'],ident)


class PublicExportTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('public_release',ROOT/'release.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
    def test_public_export_reproducible_and_exact(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            a=m.export(ROOT,Path(d)/'a.zip');b=m.export(ROOT,Path(d)/'b.zip')
            self.assertEqual(a['sha256'],b['sha256'])
            self.assertNotIn('AGENTS.md',a['files'])
            self.assertFalse(any(p.startswith(('.checkpoint/','.git/','evidence/')) for p in a['files']))
    def test_public_manifest_paths_cannot_escape_checkout(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            for path in ('../escape','.git/config','C:/escape'):
                with self.assertRaises(ValueError):m.managed_path(Path(d),path)
            (Path(d)/'linked').symlink_to(Path(d).parent,target_is_directory=True)
            with self.assertRaises(ValueError):m.managed_path(Path(d),'linked/escape')

    def test_allowlist_traversal_and_private_marker_refused(self):
        m=self.module()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'LICENSE').write_text('fixture notice');(root/'source').write_text('/'+'Users/'+'synthetic/private')
            (root/'public-export.json').write_text(json.dumps({'version':1,'files':{'LICENSE':'LICENSE','source':'source'}}))
            with self.assertRaises(ValueError):m.export(root,root/'out.zip')
            (root/'source').write_text('safe');(root/'public-export.json').write_text(json.dumps({'version':1,'files':{'LICENSE':'LICENSE','../escape':'source'}}))
            with self.assertRaises(ValueError):m.export(root,root/'out.zip')


class RepairTests(unittest.TestCase):
    def test_idempotent_repair_preserves_customization_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            cmd=[sys.executable,str(ROOT/'install.py'),'--skills-dir',d,'--repair']
            subprocess.run(cmd,check=True,capture_output=True)
            target=Path(d)/'task-checkpoint';before=(target/'.install-receipt.json').read_bytes()
            subprocess.run(cmd,check=True,capture_output=True)
            self.assertEqual(before,(target/'.install-receipt.json').read_bytes())
            (target/'SKILL.md').write_text('customized')
            self.assertEqual(subprocess.run(cmd,capture_output=True).returncode,2)
            self.assertEqual((target/'SKILL.md').read_text(),'customized')
