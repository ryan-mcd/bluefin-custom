"""Platform model management without WebUI configuration or secret arguments."""
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from test_ai_platform import ASSETS,configuration,platform,runtime
from bluefin_ai_models import ModelManager


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.base=self.home/'config';self.base.mkdir()
        self.models=self.home/'models';self.models.mkdir()
        self.config=configuration();self.config['prepare_on_boot']=['small']
        self.manager=ModelManager(self.config,self.base,self.models,lambda *a,**kw:None,platform.write)

    def test_boot_prepares_only_bootstrap_installed_and_selected_models(self):
        self.assertEqual(self.manager.boot_profiles(),['small'])
        self.manager.remember({'large'})
        self.assertEqual(self.manager.boot_profiles(),['small','large'])
        self.manager.remember(set())
        (self.base/'selection.json').write_text('{"profile":"large"}')
        self.assertEqual(self.manager.boot_profiles(),['small','large'])
        (self.base/'selection.json').write_text('{"profile":"retired"}')
        self.assertEqual(self.manager.boot_profiles(),['small'])

    def test_empty_boot_policy_does_not_download_the_fallback_model(self):
        self.config['prepare_on_boot']=[]
        self.assertEqual(self.manager.boot_profiles(),[])

    def test_general_cli_alias_resolves_the_saved_selection(self):
        (self.base/'selection.json').write_text('{"profile":"large"}')
        self.assertEqual(self.manager.resolve('local/general'),'large')

    def test_context_selection_requires_an_installed_matching_profile(self):
        self.config['profiles']['large']['context_tokens']=65536
        self.assertEqual(self.manager.resolve('coding',65536),'large')
        with self.assertRaises(ValueError):self.manager.resolve('coding',1000000)
        with self.assertRaises(ValueError):self.manager.resolve('../../other')

    def test_plan_has_no_installation_or_download_side_effect(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.manager.plan(['large'])
        self.assertIn('Unverified/missing weights',output.getvalue())
        self.assertFalse((self.base/'installed-models.json').exists())

    def test_remove_preserves_shared_weights_and_never_removes_bootstrap(self):
        self.manager.remember({'large'})
        (self.models/'test.gguf').write_bytes(b'test')
        self.manager.remove_files('large')
        self.assertEqual(self.manager.installed(),set())
        self.assertTrue((self.models/'test.gguf').exists())
        with self.assertRaises(ValueError):self.manager.remove_files('small')

    def test_remove_deletes_only_unused_model_weights_and_optional_named_cache(self):
        artifact=dict(name='large.gguf',bytes=4,sha256='c'*64)
        self.config['profiles']['large']['artifacts']=[artifact]
        self.manager.remember({'large'})
        (self.models/'large.gguf').write_bytes(b'test')
        run=unittest.mock.Mock();self.manager.run=run
        with patch('bluefin_ai_models.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            self.manager.remove_files('large',purge_cache=True)
        self.assertFalse((self.models/'large.gguf').exists())
        run.assert_any_call('podman','volume','rm','bluefin-runtime-large-cache',timeout=60)

    def test_selected_default_persists_without_eager_restart_load(self):
        events=[];selection=self.base/'selection.json'
        controller=runtime.Controller(self.config,manage=lambda *a:events.append(a),memory=lambda:200*runtime.GIB,
                                      health=lambda p:True,preflight=lambda p:None,selection_path=selection)
        controller.control('select','large')
        with controller.lease('general') as profile:self.assertEqual(profile['backend_model'],'large')
        self.assertEqual(json.loads(selection.read_text()),{'profile':'large'})
        self.assertEqual(selection.stat().st_mode & 0o777,0o600)
        events.clear()
        restored=runtime.Controller(self.config,manage=lambda *a:events.append(a),selection_path=selection)
        self.assertEqual(restored.default,'large');self.assertIsNone(restored.current);self.assertFalse(events)

    def test_failed_selection_keeps_existing_default_and_model(self):
        controller=runtime.Controller(self.config,manage=lambda *a:None,memory=lambda:200*runtime.GIB,
                                      health=lambda p:True,preflight=lambda p:None,selection_path=self.base/'selection.json')
        controller.control('select','small')
        before=(self.base/'selection.json').read_bytes()
        def missing(profile):
            if profile['backend_model']=='large':raise runtime.Rejected(503,'fetch first')
        controller.preflight=missing
        with self.assertRaises(runtime.Rejected):controller.control('select','large')
        self.assertEqual(controller.default,'small');self.assertEqual(controller.current,'small')
        self.assertEqual((self.base/'selection.json').read_bytes(),before)

    def test_failed_selection_persistence_restores_the_previous_runtime(self):
        controller=runtime.Controller(self.config,manage=lambda *a:None,memory=lambda:200*runtime.GIB,
                                      health=lambda p:True,preflight=lambda p:None,selection_path=self.base/'selection.json')
        controller.control('select','small')
        before=(self.base/'selection.json').read_bytes()
        with patch.object(runtime.os,'replace',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):controller.control('select','large')
        self.assertEqual(controller.current,'small');self.assertEqual(controller.default,'small')
        self.assertEqual((self.base/'selection.json').read_bytes(),before)

    def test_pin_auto_and_unload_keep_the_selected_general_target(self):
        self.config['aliases']['other']='small'
        controller=runtime.Controller(self.config,manage=lambda *a:None,memory=lambda:200*runtime.GIB,
                                      health=lambda p:True,preflight=lambda p:None)
        controller.control('select-pin','large')
        with controller.lease('general') as profile:self.assertEqual(profile['backend_model'],'large')
        with self.assertRaises(runtime.Rejected):
            with controller.lease('other'):pass
        controller.control('auto');self.assertIsNone(controller.pin);self.assertEqual(controller.default,'large')
        controller.control('unload');self.assertIsNone(controller.current);self.assertEqual(controller.default,'large')

    def test_remove_selected_is_refused_and_maintenance_holds_admission_gate(self):
        entered=threading.Event();release=threading.Event()
        def retire(*_args):entered.set();release.wait(2)
        controller=runtime.Controller(self.config,manage=lambda *a:None,retire=retire,preflight=lambda p:None)
        with self.assertRaises(runtime.Rejected):controller.control('remove','small')
        thread=threading.Thread(target=controller.control,args=('remove','large'));thread.start()
        self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(runtime.Rejected):
                with controller.lease('general'):pass
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_cli_client_uses_private_admin_key_over_authenticated_local_http(self):
        key='a'*64;(self.base/'admin-key').write_text(key)
        controller=runtime.Controller(self.config,manage=lambda *a:None,memory=lambda:200*runtime.GIB,
                                      health=lambda p:True,preflight=lambda p:None,selection_path=self.base/'selection.json')
        server=ThreadingHTTPServer(('127.0.0.1',0),runtime.Handler)
        server.controller=controller;server.admin_key=key;server.inference_key='b'*64
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.manager.port=server.server_port
        result=self.manager.control('select','large')
        self.assertEqual(result['selected'],'large');self.assertNotIn(key,json.dumps(result))
        self.assertEqual(self.manager.control()['selected'],'large')
        (self.base/'admin-key').write_text('b'*64)
        with self.assertRaisesRegex(RuntimeError,'Unauthorized'):self.manager.control('select','small')

    def test_webui_stays_on_one_alias_and_boot_has_no_production_download(self):
        config=platform.resolve_images(json.loads((ASSETS/'platform.json').read_text()),ASSETS/'Dockerfile')
        self.assertNotIn('webui_aliases',config)
        self.assertEqual(config['prepare_on_boot'],[])
        self.assertIsNone(config['aliases']['general'])

if __name__=='__main__':unittest.main()
