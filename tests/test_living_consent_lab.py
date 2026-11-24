"""Controlled HTTP negative tests. These are NOT the button-only browser evidence."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from tools.living_consent_lab import (init, custodian, configuration, Lab, Handler, LoopbackServer,
    ROLES, PASSWORD, MARKER, UI_FLAG)


class ConsentHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='lc21-',dir='/tmp'); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/'new'
        env = patch.dict(os.environ); env.start(); self.addCleanup(env.stop)
        init(self.root); self.cfg = configuration(self.root); os.environ.update(self.cfg)
        self.custody = custodian(self.root,self.cfg); self.custody.__enter__()
        self.addCleanup(lambda:self.custody.__exit__(None,None,None))
        os.environ[UI_FLAG]='true'
        self.server = LoopbackServer(('127.0.0.1',0),Handler); self.server.lab=Lab(self.root)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close(),thread.join(5)))
        self.port=self.server.server_port; self.origin='http://127.0.0.1:'+str(self.port)
        self.cookies={};self.csrf={}
        for role,username in ROLES.items():
            status,body,headers=self.request('POST','/api/login',{'username':username,'password':PASSWORD})
            self.assertEqual(status,200);self.cookies[role]=headers['Set-Cookie'].split(';')[0]
            self.csrf[role]=self.request('GET','/api/state?scenario=positive',role=role)[1]['csrf']

    def request(self,method,path,body=None,role=None,headers=None):
        h={'Origin':self.origin,'Content-Type':'application/json'}
        if role:h.update(Cookie=self.cookies[role],**{'X-CSRF-Token':self.csrf.get(role,'')})
        h.update(headers or {})
        client=http.client.HTTPConnection('127.0.0.1',self.port,timeout=30)
        client.request(method,path,json.dumps(body) if body is not None else None,h)
        response=client.getresponse();raw=response.read();out=dict(response.getheaders())
        status=response.status;client.close()
        return status,json.loads(raw) if out['Content-Type'].startswith('application/json') else raw.decode(),out

    def act(self,role,action,data=None,scenario='positive'):
        return self.request('POST','/api/action',{'scenario':scenario,'action':action,'data':data or {}},role)

    def preview(self,scenario='positive'):
        self.assertEqual(self.act('A','allow',scenario=scenario)[0],200)
        self.assertEqual(self.act('B','match',scenario=scenario)[0],200)
        self.assertIn(MARKER,json.dumps(self.act('B','search',scenario=scenario)[1]))
        status,preview,_=self.act('B','preview',{'recipient':'C'},scenario)
        self.assertEqual(status,200);return preview

    def test_loopback_explicit_activation_host_and_origin(self):
        with self.assertRaises(ValueError):LoopbackServer(('0.0.0.0',0),Handler)
        with patch.dict(os.environ,{UI_FLAG:'false'}):
            with self.assertRaises(ValueError):Lab(self.root)
        self.assertEqual(self.request('GET','/api/state?scenario=positive',role='A',headers={'Host':'evil.invalid'})[0],403)
        self.assertEqual(self.request('POST','/api/action',{'scenario':'positive','action':'allow'},'A',{'Origin':'https://evil.invalid'})[0],403)

    def test_csrf_roles_no_session_and_no_generic_write_route(self):
        self.assertEqual(self.request('GET','/api/state?scenario=positive')[0],401)
        self.assertEqual(self.request('POST','/api/action',{'scenario':'positive','action':'allow'},'A',{'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.act('C','allow')[0],403)
        self.assertEqual(self.act('A','search')[0],403)
        self.assertEqual(self.request('POST','/api/prepare',{},'C')[0],404)

    def test_role_query_header_and_cookie_do_not_change_authenticated_context(self):
        for role in ROLES:
            expected=self.request('GET','/api/state?scenario=positive',role=role)[1]
            other='B' if role!='B' else 'A'
            status,observed,_=self.request('GET','/api/state?scenario=positive&role='+other,
                role=role,headers={'X-Role':other,'Cookie':self.cookies[role]+'; role='+other})
            self.assertEqual(status,200);self.assertEqual(observed,expected)
            self.assertEqual(observed['role'],role)

    def test_no_search_side_effect_or_permission_without_click_command(self):
        for _ in range(2):
            for role in ROLES:self.request('GET','/api/state?scenario=positive',role=role)
        with self.server.lab.app.store._locked_transaction() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT kind FROM living_records')},{'NOTE'})
        self.assertEqual(self.act('B','search')[0],409)
        self.assertNotIn(MARKER,json.dumps(self.request('GET','/api/state?scenario=positive',role='A')[1]))

    def test_unopened_common_view_and_withdrawn_open_never_return_task(self):
        p=self.preview('revoke')
        self.assertEqual(self.act('B','send',{'nonce':p['nonce'],'recipient':'C','text':p['task']},'revoke')[0],200)
        status,state,headers=self.request('GET','/api/state?scenario=revoke',role='C')
        self.assertEqual(headers['Cache-Control'],'no-store');self.assertNotIn('task',json.dumps(state))
        did=state['data']['inbox'][0]['id']
        self.assertNotIn('task',json.dumps(self.request('GET','/api/view?scenario=revoke',role='C')[1]))
        self.assertEqual(self.act('C','contact',{'id':did},'revoke')[0],409)
        self.assertEqual(self.act('A','revoke',scenario='revoke')[0],200)
        status,value,_=self.act('C','open',{'id':did},'revoke')
        self.assertNotEqual(status,200);self.assertEqual(value,{'error':'Приглашение недоступно'})

    def test_author_responses_identical_unopened_opened_declined(self):
        p=self.preview('decline');self.act('B','send',{'nonce':p['nonce'],'recipient':'C','text':p['task']},'decline')
        did=self.request('GET','/api/state?scenario=decline',role='C')[1]['data']['inbox'][0]['id']
        snapshots=[]
        def snapshot():return [self.request('GET',path+'?scenario=decline',role='A')[1] for path in ('/api/state','/api/view')]
        snapshots.append(snapshot())
        status,value,_=self.act('C','open',{'id':did},'decline');self.assertEqual(status,200)
        self.assertIn('task',value);self.assertIn('receipt',value);self.assertNotIn(MARKER,json.dumps(value))
        snapshots.append(snapshot())
        self.assertEqual(self.act('C','decline',{'id':did},'decline')[0],200)
        snapshots.append(snapshot());self.assertEqual(snapshots[0],snapshots[1]);self.assertEqual(snapshots[1],snapshots[2])
        self.assertTrue(snapshots[2][0]['data']['can_revoke'])

    def test_recipient_and_text_changes_invalidate_confirmation(self):
        p=self.preview()
        self.act('B','invalidate',{'nonce':p['nonce']})
        self.assertEqual(self.act('B','send',{'nonce':p['nonce'],'recipient':'C','text':p['task']})[0],409)
        p=self.act('B','preview',{'recipient':'C'})[1]
        self.assertEqual(self.act('B','send',{'nonce':p['nonce'],'recipient':'A','text':p['task']})[0],409)
        p=self.act('B','preview',{'recipient':'C'})[1]
        self.assertEqual(self.act('A','edit',{'text':'Исправленная синтетическая просьба о велосипеде'})[0],200)
        self.assertEqual(self.act('B','send',{'nonce':p['nonce'],'recipient':'C','text':p['task']})[0],409)

    def test_no_audience_expansion_when_volunteer_offer_is_blocked(self):
        p=self.preview();self.act('B','send',{'nonce':p['nonce'],'recipient':'C','text':p['task']})
        did=self.request('GET','/api/state?scenario=positive',role='C')[1]['data']['inbox'][0]['id']
        self.act('C','open',{'id':did});self.act('C','contact',{'id':did})
        status,value,_=self.act('C','offer',{'id':did})
        self.assertEqual(status,200);self.assertEqual(value['blocker'],'HELP_AUDIENCE_REQUIRED')
        with self.server.lab.app.store._locked_transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM help_offers').fetchone()[0],0)
            c=db.execute('SELECT blago_user_id FROM web_credentials WHERE username=?',(ROLES['C'],)).fetchone()[0]
            self.assertEqual(db.execute('SELECT count(*) FROM help_audience WHERE member_id=?',(c,)).fetchone()[0],0)
