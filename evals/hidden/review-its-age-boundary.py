from itsdangerous import TimestampSigner, SignatureExpired
from datetime import timezone
class ClockSigner(TimestampSigner):
    now = 100
    def get_timestamp(self): return self.now
s = ClockSigner('fixture-key')
token = s.sign('payload')
s.now = 110
value, stamp = s.unsign(token, max_age=10, return_timestamp=True)
assert value == b'payload' and stamp.tzinfo == timezone.utc
for now in [111, 99]:
    s.now = now
    try: s.unsign(token, max_age=10)
    except SignatureExpired: pass
    else: raise AssertionError('invalid age accepted')

