"""Source identity, format discovery, dynamic lifecycle and cleanup regressions."""
import copy
import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ai_platform import ASSETS, configuration, platform, runtime
import bluefin_ai_huggingface as hf
from bluefin_ai_models import ModelManager, artifact_ready


def gguf(architecture='test', context=131072):
    meta = {'general.architecture': architecture, architecture+'.context_length': context,
            architecture+'.block_count': 2, architecture+'.embedding_length': 16,
            architecture+'.attention.head_count': 2, architecture+'.attention.head_count_kv': 1}
    def string(value):
        raw=value.encode();return struct.pack('<Q',len(raw))+raw
    data=b'GGUF'+struct.pack('<IQQ',3,0,len(meta))
    for key,value in meta.items():
        data+=string(key)+struct.pack('<I',8 if isinstance(value,str) else 4)
        data+=string(value) if isinstance(value,str) else struct.pack('<I',value)
    return data+b'\0'*1000


class FakeHub:
    def __init__(self, files=None):
        self.payloads=files or {'weights.gguf':gguf()}
        self.requests=[]
    def info(self, repo, revision):
        self.requests.append((repo,revision))
        return {'sha':'a'*40,'siblings':[dict(rfilename=name,size=len(data),lfs=dict(sha256=hashlib.sha256(data).hexdigest()))
                                       for name,data in self.payloads.items()]}
    def file(self, repo, revision, name, prefix=False):
        assert revision=='a'*40
        return self.payloads[name]


def response(data):
    stream=io.BytesIO(data);stream.url='https://huggingface.co/file';return stream


def source_fixture():
    return hf.resolve_source('any-publisher/any-model',hub=FakeHub())


def release():
    return platform.validate(platform.resolve_images(json.loads((ASSETS/'platform.json').read_text()),ASSETS/'Dockerfile'))


class SourceTests(unittest.TestCase):
    def test_arbitrary_publisher_exact_source_and_revision_are_retained(self):
        hub=FakeHub()
        source=hf.resolve_source('hf://someone/model',revision='release-branch',filename='weights.gguf',hub=hub)
        self.assertEqual(hub.requests,[('someone/model','release-branch')])
        self.assertEqual(source['repo'],'someone/model');self.assertEqual(source['revision'],'a'*40)
        self.assertEqual(source['file'],'weights.gguf');self.assertEqual(source['max_context'],131072)
        self.assertIn('/resolve/'+'a'*40+'/',source['artifacts'][0]['url'])

    def test_multiple_quantizations_are_never_selected_implicitly(self):
        hub=FakeHub({'model-Q4.gguf':gguf(),'model-Q8.gguf':gguf()})
        with self.assertRaisesRegex(ValueError,'exact --file'):
            hf.resolve_source('owner/model',hub=hub)
        source=hf.resolve_source('owner/model',filename='model-Q8.gguf',hub=hub)
        self.assertEqual(source['file'],'model-Q8.gguf')

    def test_split_gguf_selects_every_shard_and_preserves_original_names(self):
        files={f'sub/model-0000{i}-of-00002.gguf':gguf() for i in (1,2)}
        source=hf.resolve_source('owner/split',hub=FakeHub(files))
        self.assertEqual(len(source['artifacts']),2)
        self.assertTrue(source['artifacts'][1]['name'].endswith('/sub/model-00002-of-00002.gguf'))
        with self.assertRaisesRegex(ValueError,'missing GGUF shards'):
            hf.resolve_source('owner/split',hub=FakeHub({next(iter(files)):gguf()}))

    def test_native_checkpoint_preserves_snapshot_and_excludes_executable_code(self):
        files={'config.json':json.dumps(dict(model_type='test',max_position_embeddings=32768,num_hidden_layers=2,
                                            num_attention_heads=2,hidden_size=16)).encode(),
               'model.safetensors':b'weights','tokenizer.json':b'{}','modeling_test.py':b'raise Exception()',
               'pytorch_model.bin':b'pickle'}
        hub=FakeHub(files)
        original=hub.info
        def info(*args):
            result=original(*args)
            for item in result['siblings']:
                if item['rfilename'].endswith('.json'):
                    raw=files[item['rfilename']];item.pop('lfs')
                    item['blobId']=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw,usedforsecurity=False).hexdigest()
            return result
        hub.info=info
        source=hf.resolve_source('owner/native',hub=hub)
        self.assertEqual(source['format'],'safetensors')
        self.assertEqual({a['name'].split('/')[-1] for a in source['artifacts']}, {'config.json','model.safetensors','tokenizer.json'})
        name,profile=hf.make_profile(source,release()['runtime_images'])
        self.assertEqual(profile['engine'],'vllm');self.assertIn('/models/'+source['directory'],profile['command'])
        self.assertNotIn('--trust-remote-code',profile['command'])

    def test_gguf_and_native_formats_are_not_silently_converted(self):
        with self.assertRaisesRegex(ValueError,'native safetensors'):
            hf.make_profile(source_fixture(),release()['runtime_images'],runtime='vllm')
        native=dict(source_fixture(),format='safetensors')
        with self.assertRaisesRegex(ValueError,'Native checkpoints'):
            hf.make_profile(native,release()['runtime_images'],runtime='strata')

    def test_urls_conflicts_and_paths_are_validated(self):
        self.assertEqual(hf.parse_source('https://huggingface.co/o/r/blob/release/sub/a.gguf'),('o/r','release','sub/a.gguf'))
        for bad in ('../secret','a/../x','/absolute','a//b','a%h','a\nunit'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):hf.relative_name(bad)
        with self.assertRaises(ValueError):hf.parse_source('https://evil.example/o/r')
        with self.assertRaises(ValueError):hf.parse_source('https://huggingface.co/o/r/resolve/main/a.gguf',revision='other')

    def test_gguf_parser_rejects_truncation_and_invalid_types(self):
        self.assertEqual(hf.gguf_metadata(gguf())['test.context_length'],131072)
        for data in (b'not gguf',gguf()[:30]):
            with self.assertRaises(ValueError):hf.gguf_metadata(data)

    def test_context_is_bounded_by_the_source_and_memory_admission(self):
        source=source_fixture();images=release()['runtime_images']
        _,profile=hf.make_profile(source,images)
        self.assertEqual(profile['context_tokens'],65536)
        with self.assertRaisesRegex(ValueError,'exceeds source'):hf.make_profile(source,images,context=262144)
        source['max_context']=8192
        self.assertEqual(hf.make_profile(source,images)[1]['context_tokens'],8192)
        source['max_context']=None
        with self.assertRaisesRegex(ValueError,'declare context'):hf.make_profile(source,images)
        self.assertEqual(hf.make_profile(source,images,context=32768)[1]['context_tokens'],32768)



class DynamicLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.base=self.home/'config';self.base.mkdir()
        self.config=release();(self.base/'controller.json').write_text(json.dumps(self.config))
        self.models=self.home/'.local/share/bluefin-ai/models'
        self.name,self.profile=hf.make_profile(source_fixture(),self.config['runtime_images'])

    def save(self):
        with patch.object(platform,'BASE',self.base),patch.object(Path,'home',return_value=self.home),patch.object(platform,'run'):
            platform.save_profile(self.name,self.profile)

    def test_runtime_registry_reload_adds_a_model_without_restarting_current_runtime(self):
        events=[]
        def refresh():return platform.validate(hf.merge_registry(self.config,self.base))
        controller=runtime.Controller(self.config,manage=lambda *a:events.append(a),health=lambda p:True,
                                      memory=lambda:128*hf.GIB,preflight=lambda p:None,refresh=refresh)
        with self.assertRaises(runtime.Rejected):
            with controller.lease('general'):pass
        self.save();controller.control('reload');controller.control('select',self.name)
        with controller.lease('general') as profile:self.assertEqual(profile['source']['repo'],'any-publisher/any-model')
        events.clear();controller.control('reload');self.assertFalse(events)
        self.assertEqual(controller.status()['runtime'],'ramalama')

    def test_source_can_switch_between_ramalama_and_strata_with_shared_weights(self):
        second,strata=hf.make_profile(source_fixture(),self.config['runtime_images'],runtime='strata')
        cfg=dict(self.config,profiles={self.name:self.profile,second:strata})
        events=[]
        controller=runtime.Controller(cfg,manage=lambda *a:events.append(a),health=lambda p:True,
                                      memory=lambda:128*hf.GIB,preflight=lambda p:None)
        controller.control('select',self.name);controller.control('select',second)
        self.assertLess(events.index(('stop','bluefin-runtime-'+self.name+'.service')),
                        events.index(('start','bluefin-runtime-'+second+'.service')))
        self.assertEqual(self.profile['artifacts'],strata['artifacts'])
        self.assertNotIn('MODEL',strata['environment']);self.assertNotIn('FAMILY',strata['environment'])
        self.assertEqual(controller.status()['runtime'],'strata')

    def test_official_cache_file_is_hardlinked_into_verified_serving_view(self):
        artifact=self.profile['artifacts'][0]
        cache=self.home/'blob';cache.write_bytes(gguf())
        with patch.object(platform,'download_artifact',return_value=cache) as download:
            platform.model_artifact(self.models,artifact)
        self.assertTrue(artifact_ready(self.models,artifact))
        self.assertEqual((self.models/artifact['name']).stat().st_ino,cache.stat().st_ino)
        download.assert_called_once_with(artifact,None)
        self.assertFalse(list(self.models.rglob('.model-stage-*')))

    def test_corrupted_cache_file_never_publishes_a_serving_view(self):
        artifact=self.profile['artifacts'][0]
        cache=self.home/'blob';cache.write_bytes(b'X'*len(gguf()))
        with patch.object(platform,'download_artifact',return_value=cache):
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):platform.model_artifact(self.models,artifact)
        self.assertFalse(list(self.models.rglob('.model-stage-*')))
        self.assertFalse((self.models/artifact['name']).exists())

    def test_preexisting_verified_download_does_not_require_a_cache_redownload(self):
        artifact=self.profile['artifacts'][0]
        path=hf.model_path(self.models,artifact['name']);path.parent.mkdir(parents=True);path.write_bytes(gguf())
        with patch.object(platform,'download_artifact') as download:
            platform.model_artifact(self.models,artifact)
        download.assert_not_called();self.assertTrue(artifact_ready(self.models,artifact))

    def test_removal_drops_registry_variants_without_touching_other_repositories(self):
        self.save()
        other_source=hf.resolve_source('another/model',hub=FakeHub())
        other,profile=hf.make_profile(other_source,self.config['runtime_images'])
        with patch.object(platform,'BASE',self.base),patch.object(Path,'home',return_value=self.home),patch.object(platform,'run'):
            platform.save_profile(other,profile)
        cfg=platform.validate(hf.merge_registry(self.config,self.base))
        manager=ModelManager(cfg,self.base,self.models,lambda *a,**kw:None,platform.write)
        manager.remember({self.name,other})
        for value in cfg['profiles'].values():
            path=hf.model_path(self.models,value['artifacts'][0]['name']);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(gguf())
        with patch.object(Path,'home',return_value=self.home):manager.remove_files(self.name)
        self.assertNotIn(self.name,hf.registry_profiles(self.base));self.assertIn(other,hf.registry_profiles(self.base))
        self.assertFalse((self.models/self.profile['artifacts'][0]['name']).exists())
        self.assertTrue((self.models/profile['artifacts'][0]['name']).exists())

    def test_boot_uses_updated_runtime_images_without_changing_model_revision(self):
        self.save();changed=copy.deepcopy(self.config)
        changed['runtime_images']['ramalama']='registry.example/new@sha256:'+'c'*64
        merged=hf.merge_registry(changed,self.base)
        self.assertEqual(merged['profiles'][self.name]['image'],changed['runtime_images']['ramalama'])
        self.assertEqual(merged['profiles'][self.name]['source']['revision'],'a'*40)

    def test_clear_allows_no_selection_across_restart(self):
        cfg=dict(self.config,profiles={self.name:self.profile})
        path=self.base/'selection.json'
        controller=runtime.Controller(cfg,manage=lambda *a:None,health=lambda p:True,memory=lambda:128*hf.GIB,
                                      preflight=lambda p:None,selection_path=path)
        controller.control('select',self.name);controller.control('clear')
        self.assertFalse(path.exists());self.assertIsNone(controller.status()['selected'])
        self.assertIsNone(runtime.Controller(cfg,selection_path=path).default)

    def test_legacy_explicit_install_and_selection_are_preserved_on_image_migration(self):
        old=configuration();(self.base/'controller.json').write_text(json.dumps(old))
        (self.base/'installed-models.json').write_text('{"profiles":["large"]}')
        (self.base/'selection.json').write_text('{"profile":"small"}')
        with patch.object(platform,'BASE',self.base):platform.migrate_installed_profiles(self.config)
        self.assertEqual(set(hf.registry_profiles(self.base)),{'small','large'})


class FetchCommandTests(unittest.TestCase):
    def test_fetch_records_source_and_intent_only_for_the_requested_model(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);base=home/'config';base.mkdir()
            config=release();(base/'controller.json').write_text(json.dumps(config))
            args=SimpleNamespace(model_action='fetch',source='arbitrary/publisher',revision=None,file='weights.gguf',
                                 format='auto',runtime='ramalama',context=32768,memory_gib=None)
            source=source_fixture();source['repo']='arbitrary/publisher'
            name,profile=hf.make_profile(source,config['runtime_images'],runtime='ramalama',context=32768)
            with patch.object(platform,'BASE',base),patch.object(Path,'home',return_value=home),patch.object(platform,'run'):
                with patch.object(platform,'resolve_source',return_value=source),patch.object(platform,'prepare_models') as prepare:
                    with patch.object(ModelManager,'control') as control:platform.manage_models(args)
            self.assertEqual(set(hf.registry_profiles(base)),{name})
            self.assertEqual(json.loads((base/'installed-models.json').read_text()),{'profiles':[name]})
            prepare.assert_called_once_with([name]);control.assert_called_once_with('reload')
            self.assertFalse((base/'selection.json').exists())

    def test_plan_does_not_change_registry_or_installation_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config=release();(base/'controller.json').write_text(json.dumps(config))
            args=SimpleNamespace(model_action='plan',source='owner/model',revision=None,file='weights.gguf',
                                 format='auto',runtime='auto',context=None,memory_gib=None)
            with patch.object(platform,'BASE',base),patch.object(platform,'run'),patch.object(platform,'resolve_source',return_value=source_fixture()):
                platform.manage_models(args)
            self.assertEqual(hf.registry_profiles(base),{})
            self.assertFalse((base/'installed-models.json').exists())

class SourceChoiceRegressions(unittest.TestCase):
    def test_explicit_file_is_not_blocked_by_an_unrelated_incomplete_variant(self):
        hub=FakeHub({'chosen.gguf':gguf(),'incomplete-00001-of-00002.gguf':gguf()})
        self.assertEqual(hf.resolve_source('owner/model',filename='chosen.gguf',hub=hub)['file'],'chosen.gguf')

    def test_mixed_formats_require_a_choice_and_native_selection_is_supported(self):
        hub=FakeHub({'weights.gguf':gguf(),'config.json':b'{"model_type":"test","max_position_embeddings":8192}',
                     'model.safetensors':b'weights'})
        with self.assertRaisesRegex(ValueError,'GGUF and native'):hf.resolve_source('owner/model',hub=hub)
        self.assertEqual(hf.resolve_source('owner/model',hub=hub,format='safetensors')['format'],'safetensors')

    def test_malformed_native_config_has_an_actionable_error(self):
        with self.assertRaisesRegex(ValueError,'config.json must be an object'):
            hf.resolve_source('owner/model',hub=FakeHub({'config.json':b'[]','model.safetensors':b'weights'}))

    def test_controller_stop_cleans_up_dynamic_units_even_with_an_unreadable_registry(self):
        with patch.object(platform.sys,'argv',['bluefin-ai-platform','stop-runtimes']):
            with patch.object(platform,'run') as run:
                with patch.object(platform,'effective_config',side_effect=ValueError('broken registry')):
                    platform.main()
        run.assert_called_once_with('systemctl','--user','stop','bluefin-runtime-*.service')

    def test_fetch_an_installed_id_repairs_the_pinned_source_without_resolving_a_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config=release();(base/'controller.json').write_text(json.dumps(config))
            name,profile=hf.make_profile(source_fixture(),config['runtime_images'])
            (base/'model-registry.json').write_text(json.dumps(dict(version=1,profiles={name:profile})))
            args=SimpleNamespace(model_action='fetch',source=name,revision=None,file=None,format='auto',
                                 runtime='auto',context=None,memory_gib=None)
            with patch.object(platform,'BASE',base),patch.object(platform,'run'),patch.object(platform,'save_profile'):
                with patch.object(platform,'prepare_models') as prepare,patch.object(ModelManager,'control'):
                    with patch.object(platform,'resolve_source') as resolve:platform.manage_models(args)
            resolve.assert_not_called();prepare.assert_called_once_with([name])


if __name__=='__main__':unittest.main()
