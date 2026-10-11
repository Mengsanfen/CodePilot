from itsdangerous.encoding import base64_encode, base64_decode
from itsdangerous import URLSafeSerializer
for raw in [b'', b'a', b'ab', b'abc', bytes(range(128))]:
    token = base64_encode(raw)
    assert base64_decode(token) == raw
    assert base64_decode(token.decode('ascii')) == raw
s = URLSafeSerializer('fixture-key')
assert s.loads(s.dumps({'value': 'hello'})) == {'value': 'hello'}

