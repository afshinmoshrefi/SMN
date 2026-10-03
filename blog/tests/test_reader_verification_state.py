from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

from reader_auth import ReaderAuth,ReaderError
from reader_verification_state import export_state,cleanup_state


class Response:
    status_code=200
    def __init__(self,value):self.value=value
    def json(self):return self.value


class Transport:
    def __init__(self,identity):self.identity=identity;self.calls=[];self.allowed=True
    def request(self,method,url,**kwargs):
        self.calls.append((method,url))
        if url.endswith('/authenticate'):return Response({'user':{'id':'workos-reader'},'access_token':'provider-token-test-only'})
        if url.endswith('/authorize'):return Response(self.identity)
        return Response({k:v for k,v in dict(self.identity,can_read=self.allowed).items() if k!='reader_authority'})


class ReaderVerificationStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.now=2_000_000_000
        identity={'user_id':7,'workos_user_id':'workos-reader','workos_session_id':'provider-session',
                  'env':'dev','reader_authority':'opaque-authority-test-only',
                  'expires_at':datetime.fromtimestamp(self.now+3600,timezone.utc).isoformat()}
        self.transport=Transport(identity)
        self.auth=ReaderAuth(root/'reader/sessions',{'ENV':'dev','CLIENT_ID':'client-test','CALLBACK_URL':'https://smn-dev.trxstat.com/member/callback',
            'AUTHORITY_URL':'https://tw2-dev.trxstat.com','SERVICE_KEY':'test-key'},transport=self.transport,clock=lambda:self.now)
        pending,url=self.auth.begin('/')
        self.sid,_=self.auth.complete(pending,{'state':parse_qs(urlsplit(url).query)['state'][0],'code':'code-test-only'})
        self.output=root/'reader/qa/state.json'

    def test_export_revalidates_real_bound_authority_and_only_writes_new_cookie(self):
        result=export_state(self.auth,self.output,'https://smn-dev.trxstat.com')
        state=json.loads(self.output.read_text());qa_sid=state['cookies'][0]['value']
        self.assertNotEqual(qa_sid,self.sid);self.assertEqual(self.output.stat().st_mode&0o777,0o600)
        self.assertNotIn('opaque-authority',self.output.read_text());self.assertNotIn('provider-token',self.output.read_text())
        self.assertTrue(self.auth.session(qa_sid)['qa']);self.assertTrue(self.auth.session(self.sid))
        self.assertFalse(result['central_authority_minted']);self.assertLessEqual(state['cookies'][0]['expires'],self.now+3600)
        self.assertEqual(self.transport.calls[-1],('GET','https://tw2-dev.trxstat.com/smn-reader/entitlement'))
        self.assertTrue(self.auth.entitlement(qa_sid)[0]['can_read'])

    def test_cleanup_consumes_only_local_qa_session_not_original_authority(self):
        export_state(self.auth,self.output,'https://smn-dev.trxstat.com')
        qa_sid=json.loads(self.output.read_text())['cookies'][0]['value'];calls=len(self.transport.calls)
        result=cleanup_state(self.auth,self.output)
        self.assertEqual(len(self.transport.calls),calls);self.assertFalse(result['central_authority_revoked'])
        self.assertIsNone(self.auth.session(qa_sid));self.assertIsNotNone(self.auth.session(self.sid));self.assertFalse(self.output.exists())

    def test_revoked_entitlement_or_identity_mismatch_fails_without_export(self):
        self.transport.allowed=False
        with self.assertRaises(ReaderError):export_state(self.auth,self.output,'https://smn-dev.trxstat.com')
        self.assertFalse(self.output.exists())
        self.transport.allowed=True
        with patch.object(self.auth,'_request',return_value=dict(self.transport.identity,workos_user_id='wrong',can_read=True)):
            with self.assertRaises(ReaderError):export_state(self.auth,self.output,'https://smn-dev.trxstat.com')
        self.assertFalse(self.output.exists())

    def test_qa_session_cannot_be_cloned_or_original_session_cleaned(self):
        export_state(self.auth,self.output,'https://smn-dev.trxstat.com')
        qa_sid=json.loads(self.output.read_text())['cookies'][0]['value']
        self.auth.session(self.sid,consume=True)
        with self.assertRaises(ValueError):export_state(self.auth,self.output.with_name('second.json'),'https://smn-dev.trxstat.com')
        # Replace the private test state with a real original session id. It may
        # never be used as cleanup authorization.
        original=self.auth._save({'kind':'reader','env':'dev'},self.now+3600)
        state=json.loads(self.output.read_text());state['cookies'][0]['value']=original;self.output.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError,'only the newly issued'):cleanup_state(self.auth,self.output)
        self.assertIsNotNone(self.auth.session(original));self.assertIsNotNone(self.auth.session(qa_sid))

    def test_no_state_outside_private_root_or_production(self):
        with self.assertRaises(ValueError):export_state(self.auth,Path(self.tmp.name)/'public.json','https://smn-dev.trxstat.com')
        with self.assertRaises(ValueError):export_state(self.auth,self.output,'https://seasonalmarketnews.com')

if __name__=='__main__':unittest.main()
