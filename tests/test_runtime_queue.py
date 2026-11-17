"""Bounded FIFO admission, cancellation and one-slot generation over real HTTP."""
import http.client
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from test_ai_platform import configuration,platform,runtime


def config():
    result=configuration();result.update(max_pending=8,queue_seconds=180)
    return result


def wait_for(predicate,timeout=2):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=deadline:raise AssertionError('Timed out waiting for test condition')
        time.sleep(.01)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.controller=runtime.Controller(config(),manage=lambda *a:self.events.append(a),
                                            memory=lambda:128*runtime.GIB,health=lambda p:True,preflight=lambda p:None,
                                            catalog_ready=lambda p:True)

    def worker(self,alias='general',output=None,cancelled=None):
        output=output if output is not None else []
        def run():
            try:
                with self.controller.lease(alias,cancelled=cancelled) as profile:
                    self.assertEqual(self.controller.inflight,1)
                    output.append(profile['backend_model'])
            except Exception as exc:output.append(exc)
        thread=threading.Thread(target=run);thread.start()
        return thread,output

    def test_overlapping_requests_run_in_fifo_order_with_one_generation_slot(self):
        current=self.controller.lease('general');current.__enter__()
        result=[]
        first,_=self.worker('coding',result);wait_for(lambda:len(self.controller.pending)==1)
        second,_=self.worker('general',result);wait_for(lambda:len(self.controller.pending)==2)
        self.assertEqual(self.controller.status()['queued'],2)
        current.__exit__(None,None,None)
        first.join(3);second.join(3)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive())
        self.assertEqual(result,['large','small'])
        self.assertEqual(self.controller.inflight,0);self.assertEqual(self.controller.reserved,0)
        self.assertLess(self.events.index(('stop','bluefin-runtime-small.service')),
                        self.events.index(('start','bluefin-runtime-large.service')))

    def test_waiting_queue_is_bounded(self):
        self.controller.config['max_pending']=1
        current=self.controller.lease('general');current.__enter__()
        waiter,result=self.worker();wait_for(lambda:len(self.controller.pending)==1)
        try:
            with self.assertRaises(runtime.Rejected) as error:
                with self.controller.lease('general'):pass
            self.assertEqual(error.exception.code,429)
            self.assertIn('queue is full',str(error.exception))
            self.assertEqual(self.controller.inflight,1)
        finally:current.__exit__(None,None,None);waiter.join(3)
        self.assertEqual(result,['small']);self.assertEqual(len(self.controller.pending),0)

    def test_queue_timeout_does_not_consume_capacity_or_interrupt_current_generation(self):
        self.controller.config['queue_seconds']=.03
        with self.controller.lease('general'):
            with self.assertRaises(runtime.Rejected) as error:
                with self.controller.lease('general'):pass
            self.assertEqual(error.exception.code,429)
            self.assertIn('Timed out',str(error.exception))
            self.assertEqual(self.controller.inflight,1);self.assertEqual(self.controller.reserved,1)
            self.assertEqual(len(self.controller.pending),0)
        self.assertEqual(self.controller.reserved,0)

    def test_cancelled_waiter_never_starts_or_generates(self):
        cancelled=threading.Event()
        with self.controller.lease('general'):
            waiter,result=self.worker('coding',cancelled=cancelled.is_set)
            wait_for(lambda:len(self.controller.pending)==1)
            cancelled.set();waiter.join(2)
            self.assertFalse(waiter.is_alive());self.assertEqual(result[0].code,499)
            self.assertEqual(self.controller.inflight,1);self.assertEqual(len(self.controller.pending),0)
            self.assertNotIn(('start','bluefin-runtime-large.service'),self.events)

    def test_requests_coalesce_behind_one_cold_start(self):
        entered=threading.Event();release=threading.Event()
        def manage(*args):
            self.events.append(args)
            if args[0]=='start':entered.set();release.wait(3)
        self.controller.manage=manage
        first,first_result=self.worker();self.assertTrue(entered.wait(2))
        second,second_result=self.worker();wait_for(lambda:len(self.controller.pending)==1)
        try:
            self.assertEqual(len(self.controller.catalog()),2)
            status=self.controller.status();self.assertEqual(status['state'],'starting');self.assertEqual(status['queued'],1)
        finally:release.set();first.join(3);second.join(3)
        self.assertEqual(first_result,['small']);self.assertEqual(second_result,['small'])
        self.assertEqual(self.events.count(('start','bluefin-runtime-small.service')),1)

    def test_generation_waits_for_an_administrative_activation(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def manage(*args):
            self.events.append(args)
            if args[0]=='start':entered.set();release.wait(3)
        self.controller.manage=manage
        def select():
            try:self.controller.control('select','small')
            except Exception as exc:errors.append(exc)
        admin=threading.Thread(target=select);admin.start();self.assertTrue(entered.wait(2))
        waiter,result=self.worker();wait_for(lambda:self.controller.reserved==1)
        release.set();admin.join(3);waiter.join(3)
        self.assertFalse(errors);self.assertEqual(result,['small'])
        self.assertEqual(self.events.count(('start','bluefin-runtime-small.service')),1)

    def test_queue_policy_is_validated_and_old_full_overrides_get_defaults(self):
        base=config();base.pop('max_pending');base.pop('queue_seconds');platform.validate(base)
        for field,values in [('max_pending',[-1,17,True,None]),('queue_seconds',[0,301,False,.5])]:
            for value in values:
                candidate=config();candidate[field]=value
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):platform.validate(candidate)


class BlockingBackend(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append(body['model']);self.server.entered.set()
        if self.server.block_headers:self.server.release.wait(5)
        try:
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(b'data: {"token":"one"}\n\n');self.wfile.flush()
            self.server.release.wait(5)
            self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
        except OSError:pass


class HTTPQueueTests(unittest.TestCase):
    def setUp(self):
        self.backend=ThreadingHTTPServer(('127.0.0.1',0),BlockingBackend)
        self.backend.calls=[];self.backend.block_headers=False
        self.backend.entered=threading.Event();self.backend.release=threading.Event()
        threading.Thread(target=self.backend.serve_forever,daemon=True).start()
        cfg=config();cfg['profiles']['small']['port']=self.backend.server_port
        self.controller=runtime.Controller(cfg,manage=lambda *a:None,memory=lambda:128*runtime.GIB,
                                            health=lambda p:True,preflight=lambda p:None)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),runtime.Handler)
        self.server.controller=self.controller;self.server.inference_key='inference-secret';self.server.admin_key='admin-secret'
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.backend.release.set();self.server.shutdown();self.server.server_close()
        self.backend.shutdown();self.backend.server_close()

    def connection(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4)
        connection.request('POST','/v1/chat/completions',json.dumps({'model':'general','messages':[],'stream':True}),
                           {'Authorization':'Bearer inference-secret','Content-Type':'application/json'})
        return connection

    def test_overlapping_http_requests_wait_and_both_receive_responses(self):
        first=self.connection();response=first.getresponse();self.assertEqual(response.readline(),b'data: {"token":"one"}\n')
        second=self.connection();wait_for(lambda:len(self.controller.pending)==1)
        self.assertEqual(self.backend.calls,['small'])
        self.backend.release.set();self.assertIn(b'[DONE]',response.read());first.close()
        response=second.getresponse();self.assertEqual(response.status,200);self.assertIn(b'[DONE]',response.read());second.close()
        wait_for(lambda:self.controller.reserved==0)
        self.assertEqual(self.backend.calls,['small','small'])

    def test_disconnected_queued_request_is_removed_without_generating(self):
        first=self.connection();response=first.getresponse();response.readline()
        second=self.connection();wait_for(lambda:len(self.controller.pending)==1);second.close()
        wait_for(lambda:len(self.controller.pending)==0)
        self.assertEqual(self.backend.calls,['small']);self.assertEqual(self.controller.inflight,1)
        self.backend.release.set();response.read();first.close();wait_for(lambda:self.controller.reserved==0)

    def test_client_disconnect_during_sse_releases_lease_before_backend_finishes(self):
        first=self.connection();response=first.getresponse();response.readline();response.close();first.close()
        wait_for(lambda:self.controller.reserved==0)
        self.assertEqual(self.controller.inflight,0);self.assertFalse(self.backend.release.is_set())
        self.assertEqual(self.backend.calls,['small'])
        self.backend.release.set()
        second=self.connection();response=second.getresponse();self.assertEqual(response.status,200);response.read();second.close()
        self.assertEqual(self.backend.calls,['small','small'])

    def test_client_disconnect_before_upstream_headers_releases_capacity(self):
        self.backend.block_headers=True
        connection=self.connection();self.assertTrue(self.backend.entered.wait(2));connection.close()
        wait_for(lambda:self.controller.reserved==0)
        self.assertEqual(self.controller.inflight,0);self.assertFalse(self.backend.release.is_set())
        self.assertEqual(self.backend.calls,['small'])


if __name__=='__main__':unittest.main()
