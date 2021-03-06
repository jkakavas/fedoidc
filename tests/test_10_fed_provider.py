import json
import os
import shutil
from time import time

import pytest

from fedoidc.entity import FederationEntity
from fedoidc.operator import Operator
from fedoidc.provider import Provider
from fedoidc.signing_service import Signer
from fedoidc.signing_service import InternalSigningService
from fedoidc.test_utils import make_jwks_bundle
from fedoidc.test_utils import make_signed_metadata_statements
from jwkest import jws, as_unicode

from oic import rndstr
from oic.utils.authn.authn_context import AuthnBroker
from oic.utils.authn.client import verify_client
from oic.utils.authn.user import UserAuthnMethod
from oic.utils.authz import AuthzHandling
from oic.utils.keyio import KeyJar, build_keyjar
from oic.utils.sdb import SessionDB

# Create JWKS bundle
from oic.utils.userinfo import UserInfo

KEYDEFS = [
    {"type": "RSA", "key": '', "use": ["sig"]},
    {"type": "EC", "crv": "P-256", "use": ["sig"]}
]

jb = make_jwks_bundle('', ['swamid', 'sunet', 'feide', 'uninett'],
                      None, KEYDEFS)

# And the corresponding operators

operator = {}
for iss, kj in jb.items():
    _kj = KeyJar()
    _kj.issuer_keys[''] = kj.issuer_keys[iss]
    operator[iss] = Operator(keyjar=_kj, iss=iss)

# create a couple of metadata statements

MS_DEFS = [
    [
        {'request': {}, "requester": 'sunet', 'signer': 'swamid',
         'signer_add': {}}
    ],
    [
        {'request': {}, "requester": 'sunet', 'signer': 'feide',
         'signer_add': {}}
    ]
]

SMS = make_signed_metadata_statements(MS_DEFS, operator)
MS_ROOT = 'ms_dir'

if os.path.isdir(MS_ROOT):
    shutil.rmtree(MS_ROOT)

os.makedirs(MS_ROOT)

for spec in SMS:
    fname = os.path.join(MS_ROOT, spec['fo'])
    fp = open(fname, 'w')
    fp.write(spec['ms'])
    fp.close()


class DummyAuthn(UserAuthnMethod):
    def __init__(self, srv, user):
        UserAuthnMethod.__init__(self, srv)
        self.user = user

    def authenticated_as(self, cookie=None, **kwargs):
        if cookie == "FAIL":
            return None, 0
        else:
            return {"uid": self.user}, time()


AUTHN_BROKER = AuthnBroker()
AUTHN_BROKER.add("UNDEFINED", DummyAuthn(None, "username"))

# dealing with authorization
AUTHZ = AuthzHandling()
SYMKEY = rndstr(16)  # symmetric key used to encrypt cookie info

USERDB = {
    "user": {
        "name": "Hans Granberg",
        "nickname": "Hasse",
        "email": "hans@example.org",
        "verified": False,
        "sub": "user"
    },
    "username": {
        "name": "Linda Lindgren",
        "nickname": "Linda",
        "email": "linda@example.com",
        "verified": True,
        "sub": "username"
    }
}

USERINFO = UserInfo(USERDB)

SIGNER = Signer(ms_dir='ms_dir',
                signing_service=InternalSigningService(
                    'https://operator.example.com',
                    build_keyjar(KEYDEFS)[1]
                ))


class TestProvider(object):
    @pytest.fixture(autouse=True)
    def create_provider(self):
        sunet_op = 'https://www.sunet.se/op'

        _kj = build_keyjar(KEYDEFS)[1]
        fed_ent = FederationEntity(None, keyjar=_kj, iss=sunet_op,
                                   signer=SIGNER)

        self.op = Provider(sunet_op, SessionDB(sunet_op), {},
                           AUTHN_BROKER, USERINFO,
                           AUTHZ, client_authn=verify_client, symkey=SYMKEY,
                           federation_entity=fed_ent)
        self.op.baseurl = self.op.name

    def test_create_metadata_statement_request(self):
        _fe = self.op.federation_entity
        statement = self.op.create_providerinfo()
        req = _fe.create_metadata_statement_request(statement)
        assert 'signing_keys' in req

    def test_use_signing_service(self):
        _fe = self.op.federation_entity
        statement = self.op.create_providerinfo()
        req = _fe.create_metadata_statement_request(statement)

        sjwt = _fe.signer.create_signed_metadata_statement(
            req, fos=_fe.signer.metadata_statements.keys())

        assert sjwt

        # should be a signed JWT

        _js = jws.factory(sjwt)
        assert _js
        assert _js.jwt.headers['alg'] == 'RS256'

    def test_create_fed_provider_info(self):
        fedpi = self.op.create_fed_providerinfo()

        assert 'signing_keys' not in fedpi

        _js = jws.factory(fedpi['metadata_statements'][0])
        assert _js
        assert _js.jwt.headers['alg'] == 'RS256'
        _body = json.loads(as_unicode(_js.jwt.part[1]))
        assert _body['iss'] == self.op.federation_entity.signer.signing_service.iss
