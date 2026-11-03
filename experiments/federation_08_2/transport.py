"""Bounded loopback-only HTTP lab. No production routes or public listener.
A real multi-host adapter needs authenticated TLS and independent retention review.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from runtime import encode, Failure, Unknown

MAX_REQUEST = 2_200_000

def server(receiver, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                if self.headers.get('Transfer-Encoding'): raise Failure('WIRE')
                size = int(self.headers.get('Content-Length','0'))
                if not 0 < size <= MAX_REQUEST: raise Failure('WIRE')
                self.connection.settimeout(3)
                raw=self.rfile.read(size)
                if len(raw) != size: raise Failure('WIRE')
                def pairs(items):
                    out={}
                    for k,v in items:
                        if k in out: raise Failure('WIRE')
                        out[k]=v
                    return out
                req=json.loads(raw,object_pairs_hook=pairs)
                if self.path=='/challenge' and set(req)=={'envelope_hex'}:
                    result={'nonce':receiver.challenge(req['envelope_hex'])}
                elif self.path=='/deliver': result={'receipt':receiver.deliver(req)}
                elif self.path=='/revoke-challenge' and set(req)=={'revoke_hex'}:
                    result={'nonce':receiver.challenge(req['revoke_hex'],'revoke')}
                elif self.path=='/revoke' and set(req)=={'history_hex','request_nonce'}:
                    result={'receipt':receiver.revoke(**req)}
                else: raise Failure('ROUTE')
                status=200
            except Unknown: status,result=503,{'error':'UNKNOWN_RECOVERY'}
            except OSError: status,result=503,{'error':'UNKNOWN_RECOVERY'}
            except Exception: status,result=400,{'error':'REJECT'}
            data=encode(result)
            self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.end_headers()
            try:
                self.wfile.write(data); self.wfile.flush()
                if status == 200 and self.path == '/deliver': receiver.fault.hit('after_ack')
            except (BrokenPipeError,ConnectionResetError): pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

class Transport:
    def __init__(self,url):
        if not url.startswith('http://127.0.0.1:'): raise ValueError('LOOPBACK_ONLY')
        self.url=url; self.partition=False; self.drop_ack=False
    def post(self,path,body):
        if self.partition: raise TimeoutError('synthetic partition')
        with urlopen(Request(self.url+path,data=encode(body),headers={'Content-Type':'application/json'}),timeout=3) as r:
            raw=r.read(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST: raise Failure('WIRE')
        if self.drop_ack: raise TimeoutError('synthetic lost ACK')
        return json.loads(raw)
