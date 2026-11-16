"""Official client integration, resumable cache files and guarded revision cleanup."""
import copy
import errno
import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import huggingface_hub as official
import huggingface_hub.file_download as file_download
from test_ai_platform import platform
from test_ai_huggingface import gguf, source_fixture, release
import bluefin_ai_huggingface as hf
from bluefin_ai_models import ModelManager, artifact_ready


class CacheEndpoint(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_HEAD(self):self.respond(False)
    def do_GET(self):self.respond(True)
    def respond(self,body):
        # /owner/repo/resolve/<commit>/<file>; no auth or outgoing network.
        parts=self.path.split('/');revision=parts[4]
        data=self.server.data;digest=hashlib.sha256(data).hexdigest()
        start=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
        if body:self.server.ranges.append(start)
        self.send_response(206 if start else 200)
        self.send_header('X-Repo-Commit',revision)
        self.send_header('ETag','"'+digest+'"')
        self.send_header('Content-Length',str(len(data)-start))
        if start:self.send_header('Content-Range',f'bytes {start}-{len(data)-1}/{len(data)}')
        self.end_headers()
        if body and getattr(self.server,'interrupt_once',False):
            self.server.interrupt_once=False
            self.wfile.write(data[:100]);self.wfile.flush();self.close_connection=True
        elif body:self.wfile.write(data[start:])


class OfficialClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.cache=self.home/'cache';self.models=self.home/'models'
        self.server=ThreadingHTTPServer(('127.0.0.1',0),CacheEndpoint)
        self.server.data=gguf();self.server.ranges=[]
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.endpoint='http://127.0.0.1:'+str(self.server.server_port)
        self.source=source_fixture();self.artifact=self.source['artifacts'][0]
        self.real_download=official.hf_hub_download
        self.client=SimpleNamespace(try_to_load_from_cache=official.try_to_load_from_cache,
                                    hf_hub_download=lambda **kwargs:self.real_download(endpoint=self.endpoint,**kwargs),
                                    scan_cache_dir=official.scan_cache_dir)

    def download(self,artifact=None):
        with patch.object(hf,'hub_client',return_value=self.client):
            return hf.download_artifact(artifact or self.artifact,self.cache)

    def test_download_uses_the_official_revision_cache_and_reuses_cached_bytes(self):
        first=self.download();self.assertEqual(first.read_bytes(),gguf())
        self.assertIn('snapshots/'+self.source['revision'],str(first))
        ranges=list(self.server.ranges)
        self.assertEqual(self.download(),first)
        self.assertEqual(self.server.ranges,ranges)
        info=official.scan_cache_dir(self.cache)
        self.assertEqual({repo.repo_id for repo in info.repos},{self.source['repo']})

    def test_official_client_retries_an_interrupted_stream_with_a_range_request(self):
        self.server.interrupt_once=True
        with patch.object(file_download.constants,'DOWNLOAD_CHUNK_SIZE',100):
            path=self.download()
        self.assertEqual(path.read_bytes(),gguf())
        self.assertEqual(self.server.ranges,[0,100])
        self.assertFalse(list(self.cache.rglob('*.incomplete')))

    def test_command_failure_cleans_unique_partial_file_and_retry_downloads_again(self):
        def interrupt(url,temp_file,**kwargs):
            temp_file.write(gguf()[:100]);temp_file.flush()
            raise OSError('simulated command failure')
        with patch.object(file_download,'http_get',side_effect=interrupt):
            with self.assertRaisesRegex(ValueError,'Hugging Face request failed'):self.download()
        # The pinned client intentionally does not keep partial HTTP files
        # between invocations; only an in-flight retry resumes its stream.
        self.assertFalse(list(self.cache.rglob('*.incomplete')))
        self.assertEqual(self.download().read_bytes(),gguf())
        self.assertEqual(self.server.ranges,[0])

    def test_stage_failure_keeps_official_cache_for_retry(self):
        artifact=dict(self.artifact,sha256='b'*64)
        with patch.object(hf,'hub_client',return_value=self.client):
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                platform.model_artifact(self.models,artifact,cache_dir=self.cache)
        self.assertFalse((self.models/artifact['name']).exists())
        self.assertTrue(official.try_to_load_from_cache(self.source['repo'],self.source['file'],
                                                     revision=self.source['revision'],cache_dir=self.cache))

    def test_disk_reserve_does_not_charge_again_for_an_unchanged_cached_blob(self):
        first=self.download()
        second_artifact=dict(self.artifact,url=self.artifact['url'].replace('a'*40,'b'*40))
        with patch.object(hf.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            second=self.download(second_artifact)
        self.assertEqual(first.resolve(),second.resolve())
        self.assertEqual(self.server.ranges,[0])

    def test_other_revision_and_shared_blob_survive_cache_release(self):
        first=self.download()
        second_artifact=dict(self.artifact,url=self.artifact['url'].replace('a'*40,'b'*40))
        second=self.download(second_artifact)
        self.assertEqual(first.resolve(),second.resolve())
        removed=dict(artifacts=[self.artifact],hub_cache=str(self.cache))
        retained=dict(artifacts=[second_artifact],hub_cache=str(self.cache))
        hf.release_cache_revisions([removed],[retained])
        self.assertFalse(first.exists());self.assertTrue(second.exists())
        self.assertEqual(second.read_bytes(),gguf())

    def test_same_revision_is_retained_until_the_last_registered_model_releases_it(self):
        file=self.download()
        profile=dict(artifacts=[self.artifact],hub_cache=str(self.cache))
        client=Mock()
        other=dict(profile,artifacts=[dict(self.artifact,url=self.artifact['url'].replace('weights.gguf','another-quant.gguf'))])
        with patch.object(hf,'hub_client',return_value=client):hf.release_cache_revisions([profile],[other])
        client.scan_cache_dir.assert_not_called();self.assertTrue(file.exists())
        hf.release_cache_revisions([profile],[])
        self.assertFalse(file.exists())

    def test_fork_sharing_commit_does_not_evict_another_repository(self):
        first=self.download()
        fork=dict(self.artifact,url=self.artifact['url'].replace('any-publisher/any-model','another-publisher/fork'))
        other=self.download(fork)
        hf.release_cache_revisions([dict(artifacts=[self.artifact],hub_cache=str(self.cache))],[])
        self.assertTrue(first.exists());self.assertTrue(other.exists())

    def test_cross_filesystem_serving_copy_is_verified(self):
        cache=self.download()
        with patch.object(platform,'download_artifact',return_value=cache):
            with patch.object(platform.os,'link',side_effect=OSError(errno.EXDEV,'different filesystem')):
                platform.model_artifact(self.models,self.artifact,cache_dir=self.cache)
        self.assertTrue(artifact_ready(self.models,self.artifact))
        self.assertEqual((self.models/self.artifact['name']).read_bytes(),gguf())
        self.assertNotEqual((self.models/self.artifact['name']).stat().st_ino,cache.resolve().stat().st_ino)

    def test_api_metadata_and_range_reads_delegate_auth_to_official_clients(self):
        item=SimpleNamespace(rfilename='weights.gguf',size=len(gguf()),blob_id='c'*40,
                             lfs=SimpleNamespace(sha256=hashlib.sha256(gguf()).hexdigest(),size=len(gguf())))
        client=MagicMock();client.HfApi.return_value.model_info.return_value=SimpleNamespace(sha='a'*40,siblings=[item])
        stream=client.HfFileSystem.return_value.open.return_value.__enter__.return_value
        stream.read.return_value=gguf()
        with patch.object(hf,'hub_client',return_value=client):
            hub=hf.Hub();info=hub.info('owner/model','release');data=hub.file('owner/model','a'*40,'weights.gguf',True)
        client.HfApi.assert_called_once_with();client.HfFileSystem.assert_called_once_with()
        self.assertEqual(info['siblings'][0]['lfs']['sha256'],item.lfs.sha256)
        client.HfApi.return_value.model_info.assert_called_once_with('owner/model',revision='release',files_metadata=True)
        client.HfFileSystem.return_value.open.assert_called_once_with('owner/model/weights.gguf',revision='a'*40,
                                                                    mode='rb',block_size=1024*1024,cache_type='none')
        self.assertEqual(data,gguf())

    def test_errors_do_not_expose_credentials_or_signed_urls(self):
        client=Mock();client.HfApi.return_value.model_info.side_effect=RuntimeError('https://cdn/?secret=hf_private')
        with patch.object(hf,'hub_client',return_value=client):
            with self.assertRaises(ValueError) as result:hf.Hub().info('owner/model','main')
        self.assertNotIn('secret',str(result.exception));self.assertNotIn('hf_private',str(result.exception))

    def test_cache_metadata_update_does_not_restart_an_active_runtime(self):
        from test_ai_platform import configuration,runtime
        config=configuration();events=[]
        controller=runtime.Controller(config,manage=lambda *a:events.append(a),memory=lambda:256*hf.GIB,
                                      health=lambda p:True,preflight=lambda p:None)
        controller.control('select','small');events.clear()
        updated=copy.deepcopy(config);updated['profiles']['small']['hub_cache']=str(self.cache)
        controller.refresh=lambda:updated
        controller.control('reload')
        self.assertFalse(events);self.assertEqual(controller.current,'small')

    def test_insufficient_cache_space_refuses_network_transfer(self):
        client=Mock();client.hf_hub_download.return_value=SimpleNamespace(will_download=True)
        with patch.object(hf,'hub_client',return_value=client),patch.object(hf.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'Insufficient disk space'):hf.download_artifact(self.artifact,self.cache)
        client.hf_hub_download.assert_called_once()
        self.assertTrue(client.hf_hub_download.call_args.kwargs['dry_run'])


if __name__=='__main__':unittest.main()
