from itsdangerous import Signer
s = Signer('fixture-key')
assert s.validate_many(iter([s.sign('a'), b'bad', s.sign('b')])) == [True, False, True]
assert s.validate_many([]) == []
class Derived(Signer):
    def validate(self, value): return value == b'yes'
assert Derived('fixture-key').validate_many([b'yes', b'no']) == [True, False]

