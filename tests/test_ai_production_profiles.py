"""Generated runtime adapters enforce context and storage contracts."""
import copy,unittest
from test_ai_platform import platform
from test_ai_huggingface import release,source_fixture
from bluefin_ai_huggingface import make_profile

class RuntimeAdapterTests(unittest.TestCase):
    def test_all_gguf_adapters_use_exact_source_with_one_slot_and_loopback_ingress(self):
        source=source_fixture();config=release()
        for engine in ('ramalama','llamacpp','strata'):
            name,profile=make_profile(source,config['runtime_images'],runtime=engine,context=65536)
            platform.validate(dict(config,profiles={name:profile}))
            text=platform.runtime_quadlet(name,profile)
            self.assertIn('PublishPort=127.0.0.1:18110:8080',text)
            self.assertIn('Volume=%h/.local/share/bluefin-ai/models/'+source['directory'],text)
            self.assertNotIn('[Install]',text)
            if engine=='strata':
                self.assertEqual(profile['environment']['PARALLEL'],'1')
                self.assertNotIn('FAMILY',profile['environment'])
            else:
                args=profile['command'];self.assertEqual(args[args.index('--parallel')+1],'1')

    def test_context_mismatch_and_shared_file_collision_are_rejected(self):
        config=release();name,profile=make_profile(source_fixture(),config['runtime_images'])
        bad=copy.deepcopy(profile);bad['context_tokens']=32768
        with self.assertRaisesRegex(ValueError,'context/slots'):platform.validate(dict(config,profiles={name:bad}))
        bad=copy.deepcopy(profile);bad['artifacts'][0]['sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'Conflicting metadata'):
            platform.validate(dict(config,profiles={name:profile,'other':bad}))

if __name__=='__main__':unittest.main()
