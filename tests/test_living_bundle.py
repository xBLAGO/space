"""Verifier fixtures are invented unit data, NEVER browser acceptance evidence."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from tools.living_bundle_contract import (BANNER, SCHEMA, VERIFIER, BASE, REASON,
    BRANCHES, expected_matrix, redact_note, sha, wire)
from tools.living_bundle_verify import Bundle, verify, png_nonblank


def image(blank=False):
    def chunk(tag, data):
        return struct.pack('>I', len(data))+tag+data+struct.pack('>I', zlib.crc32(tag+data)&0xffffffff)
    row=b''.join(bytes((x%256, 0, 0)) if not blank else b'\0\0\0' for x in range(320))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',320,200,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+row)*200))+chunk(b'IEND',b'')


def fixture(root):
    """Complete synthetic *verifier* input; contains no actual UI observations."""
    expected=expected_matrix(); markers={'NOTE_SECRET':'NOTE_SECRET_UNIT','REQ_TEXT':'REQ_TEXT_UNIT','RECIPIENT_ID_C':'c'*64}
    text='REQ_TEXT_UNIT: complete request\nSecond line'; artifacts={}; rows=[]; actual=[]
    def save(name, data):
        (root/name).write_bytes(data); artifacts[name]=sha(data)
    save('unit-image.png', image())
    for i,e in enumerate(expected):
        present={n:p=='REQUIRED' for n,p in e['markers'].items()}
        content=BANNER+'\nUNIT FIXTURE ONLY: invented verifier observations\n'
        if present['REQ_TEXT']: content+=text+'\n'
        if present['NOTE_SECRET']: content+=markers['NOTE_SECRET']+' [MASKED]\n'
        if present['RECIPIENT_ID_C']: content+=markers['RECIPIENT_ID_C']+'\n'
        counters={'offers':0,'published_resources':0,'reservations':0}
        reset=e['branch']=='decline' and e['step'] in ('restart_cleanup_relogin','new_match_private','new_memory_search','new_preview','new_attempt_blocked')
        row={k:e[k] for k in ('branch','checkpoint','step','role','surface')}
        row.update(session_id=e['branch']+'_'+e['role']+('_fresh' if reset else '_initial'),request_id='unit_'+str(i),
            invitation_id='unit_invitation',action=e['step'] if e['role']=='C' else 'observe',
            visible_field_names=['unit'],marker_presence=present,consent_state='UNIT',
            refusal_state='NOT_OBSERVABLE',revocation_state='UNIT',
            recipient_first_text_delivery=e['role']=='C' and e['surface']=='network_response' and e['step']=='recipient_receive',
            timestamp=(datetime(2026,11,24,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat(),
            screenshot='unit-image.png' if e['surface']!='network_response' else None,
            banner_dom=True,banner_screenshot=True,request_text_sha256=sha(text.encode()) if present['REQ_TEXT'] else None,
            mask={'policy':'marker-only-v1','scan_before_mask':True,'full_note_persisted':False} if present['NOTE_SECRET'] else None,
            counters=counters if e['counters_zero_required'] else None,endpoint='/unit/state',http_status=200,response_field_names=['unit'])
        if e['surface']=='network_response':
            body={'unit':content}
            if e['step']=='contact_consent': body['counters']=counters
            if row['action']=='recipient_receive':
                body={'format':'unit','receipt':'unit','task':text,'referrer':'unit_B','contact_only':True,'resource_available':False}
            value={'request_body':{'id':'unit_invitation'} if row['action']=='recipient_decline' else {},'response_body':body}
            data=wire(value); ext='json'
        else: data=content.encode();ext='txt' if e['surface']=='visible' else 'html'
        name='unit-'+str(i)+'.'+ext;save(name,data);row['artifact']=name;rows.append(row)
        actual.append({k:e[k] for k in ('branch','step','role','surface')}|{'marker_presence':present,'observations':1})
    manifest={'schema_version':SCHEMA,'verifier_version':VERIFIER,'base_sha':BASE,
        'capture_started_at':'2026-11-24T00:00:00Z','seed':{'markers':markers,'request_text':text,'refusal_reason':REASON},
        'plan':{'author_audience_change':False},'branches':{b:{'initial_database_sha256':'a'*64,'status':'EXECUTED'} for b in BRANCHES},
        'cleanup_method':'CLOSE_BROWSER_PROCESS_AND_ALL_CONTEXTS; FRESH_LOGINS',
        'expected_matrix_sha256':sha(wire(expected)),'artifacts':artifacts,
        'local_ui':{'files':{'unit':'not-real-ui'},'sha256':sha(wire({'unit':'not-real-ui'}))},
        'target_integration':'EXECUTED','claimed_verdict':'UNIT_FIXTURE_ONLY'}
    matrix={'frozen_at':'2026-11-23T23:59:00Z','expected':expected,'actual':actual}
    def flush():
        save('audit.jsonl',b'\n'.join(wire(r) for r in rows)+b'\n');save('visibility-matrix.json',wire(matrix));save('report.md',b'# UNIT FIXTURE ONLY\nNot browser evidence.\n')
        (root/'manifest.json').write_bytes(wire(manifest))
    flush();return manifest,matrix,rows,flush


class BundleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.manifest,self.matrix,self.rows,self.flush=fixture(self.root)

    def test_complete_unit_fixture_is_consistent_not_independent_attestation(self):
        result=verify(self.root);self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['verdict'],'BUNDLE_CONSISTENT_NOT_INDEPENDENT_ATTESTATION')

    def reject(self):
        self.flush();self.assertNotEqual(verify(self.root)['exit_code'],0)

    def test_missing_role(self):
        self.rows[:]=[r for r in self.rows if r['role']!='B'];self.reject()

    def test_empty_screen(self):
        p=self.root/self.rows[0]['artifact'];p.write_bytes(b'');self.manifest['artifacts'][p.name]=sha(b'');self.reject()

    def test_blank_screenshot(self):
        data=image(True);(self.root/'unit-image.png').write_bytes(data);self.manifest['artifacts']['unit-image.png']=sha(data);self.reject()

    def test_missing_screenshot(self):
        (self.root/'unit-image.png').unlink();self.reject()

    def test_hash_damage(self):
        (self.root/self.rows[0]['artifact']).write_text('changed');self.reject()

    def test_weakened_expected_matrix(self):
        self.matrix['expected'][0]['markers']['NOTE_SECRET']='OPTIONAL';self.reject()

    def test_false_pass_on_blocked_branch(self):
        self.manifest['branches']['consent']['status']='BLOCKED';self.manifest['claimed_verdict']='PASS';self.reject()

    def test_missing_first_delivery(self):
        for r in self.rows:r['recipient_first_text_delivery']=False
        self.reject()

    def test_marker_flags_cannot_hide_full_dom_leak(self):
        r=next(r for r in self.rows if r['role']=='A' and r['surface']=='full_dom')
        p=self.root/r['artifact'];data=p.read_bytes()+b'<div hidden>NOTE_SECRET_UNIT</div>'
        p.write_bytes(data);self.manifest['artifacts'][p.name]=sha(data);self.reject()

    def test_extra_delivery_fields(self):
        r=next(r for r in self.rows if r['recipient_first_text_delivery']);p=self.root/r['artifact']
        data=json.loads(p.read_bytes());data['response_body']['extra']='forbidden'
        p.write_bytes(wire(data));self.manifest['artifacts'][p.name]=sha(wire(data));self.reject()

    def test_reason_not_collected(self):
        r=next(r for r in self.rows if r['action']=='recipient_decline' and r['surface']=='network_response');p=self.root/r['artifact']
        data=json.loads(p.read_bytes());data['request_body']['reason']='never'
        p.write_bytes(wire(data));self.manifest['artifacts'][p.name]=sha(wire(data));self.reject()

    def test_json_key_is_also_a_disclosure_surface(self):
        r=next(r for r in self.rows if r['role']=='A' and r['surface']=='network_response');p=self.root/r['artifact']
        data=json.loads(p.read_bytes());data['response_body']['NOTE_SECRET_UNIT']='hidden in key'
        p.write_bytes(wire(data));self.manifest['artifacts'][p.name]=sha(wire(data));self.reject()

    def test_missing_whole_request_cannot_pass_on_marker_only(self):
        r=next(r for r in self.rows if r['recipient_first_text_delivery']);p=self.root/r['artifact']
        data=json.loads(p.read_bytes());data['response_body']['task']='REQ_TEXT_UNIT'
        p.write_bytes(wire(data));self.manifest['artifacts'][p.name]=sha(wire(data));self.reject()

    def test_session_cannot_be_shared_across_branches(self):
        for r in self.rows:r['session_id']=r['session_id'].replace(r['branch']+'_','')
        self.reject()

    def test_no_auth_fields(self):
        self.manifest['cookies']='not even redacted';self.reject()

    def test_different_genesis(self):
        self.manifest['branches']['revoke']['initial_database_sha256']='b'*64;self.reject()

    def test_restart_requires_new_context(self):
        for r in self.rows:r['session_id']=r['session_id'].replace('_fresh','_initial')
        self.reject()

    def test_only_bundle_files_no_sqlite_paths_or_escape(self):
        for name in ('../secret.json','db.sqlite3','/tmp/a.json'):
            with self.subTest(name=name),self.assertRaises(ValueError):Bundle(self.root).read(name)

    def test_redaction_positive_control_not_invented(self):
        text='private NOTE_SECRET_UNIT sentence'
        masked,proof=redact_note(text,text,'NOTE_SECRET_UNIT')
        self.assertEqual(masked,'NOTE_SECRET_UNIT [MASKED]');self.assertTrue(proof['marker_seen_before_mask'])
        self.assertFalse(proof['full_note_persisted'])
        masked,proof=redact_note('no note here',text,'NOTE_SECRET_UNIT')
        self.assertFalse(proof['marker_seen_before_mask']);self.assertNotIn('NOTE_SECRET_UNIT',masked)
        with self.assertRaises(ValueError):redact_note('NOTE_SECRET_UNIT escaped differently',text,'NOTE_SECRET_UNIT')


if __name__=='__main__':unittest.main()
