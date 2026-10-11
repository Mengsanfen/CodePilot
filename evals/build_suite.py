"""Authoring source for the fixed repository benchmark; run from repository root."""
import json
from pathlib import Path

REPOS = {
    'click': {'url': 'https://github.com/pallets/click.git', 'sha': '934813e4d421071a1b3db3973c02fe2721359a6e', 'python_path': 'src'},
    'itsdangerous': {'url': 'https://github.com/pallets/itsdangerous.git', 'sha': '096c8d42545d3b68ea21a4f890fb2b2d8979c0bd', 'python_path': 'src'},
    'httpx': {'url': 'https://github.com/encode/httpx.git', 'sha': '26d48e0634e6ee9cdc0533996db289ce4b430177', 'python_path': '.'},
}
TASKS = []
HIDDEN = Path('evals/hidden')
HIDDEN.mkdir(parents=True, exist_ok=True)


def edit(path, before, after):
    return {'path': path, 'before': before, 'after': after}


def add(name, repo, kind, prompt, checks='', **extra):
    hidden = HIDDEN / (name + '.py')
    if checks:
        hidden.write_text(checks + '\n', encoding='utf-8')
    TASKS.append({'id': name, 'repo': repo, 'base_sha': REPOS[repo]['sha'], 'type': kind,
        'prompt': prompt, 'budget': {'max_seconds': 240, 'max_model_calls': 20},
        'verification': {'command': 'python /projects/task/__acceptance.py',
                         'hidden_files': [hidden.as_posix()] if checks else []}, **extra})


bugs = [
 ('click-bool', 'click', edit('src/click/types.py', 'norm = value.strip().lower()', 'norm = value.lower()'),
  '修复 Click BOOL 参数转换对首尾空白的回归；保留已有布尔输入、大小写和非法输入的行为。',
  '''import click
from click.testing import CliRunner
assert click.BOOL.convert('  YES  ', None, None) is True
assert click.BOOL.convert(' OFF\\t', None, None) is False
assert click.BOOL.convert(True, None, None) is True
try: click.BOOL.convert('maybe', None, None)
except click.BadParameter: pass
else: raise AssertionError('invalid boolean accepted')
@click.command()
@click.option('--enabled', type=click.BOOL)
def cli(enabled): click.echo(str(enabled))
result = CliRunner().invoke(cli, ['--enabled', ' true '])
assert result.exit_code == 0 and result.output.strip() == 'True'
'''),
 ('click-normalize', 'click', edit('src/click/parser.py', 'return f"{prefix}{ctx.token_normalize_func(opt)}"', 'return ctx.token_normalize_func(opt)'),
  '修复启用 token_normalize_func 时选项名称归一化丢失前缀的问题；短/长选项和未配置钩子的行为都需要保留。',
  '''import click
from click.parser import normalize_opt
ctx = click.Context(click.Command('demo'), token_normalize_func=str.lower)
assert normalize_opt('--HELP', ctx) == '--help'
assert normalize_opt('-V', ctx) == '-v'
assert normalize_opt('--HELP', None) == '--HELP'
from click.testing import CliRunner
@click.command(context_settings={'token_normalize_func': str.lower})
@click.option('--name')
def cli(name): click.echo(name)
assert CliRunner().invoke(cli, ['--NAME', 'Ada']).output.strip() == 'Ada'
'''),
 ('its-age-boundary', 'itsdangerous', edit('src/itsdangerous/timed.py', 'if age > max_age:', 'if age >= max_age:'),
  '修复 TimestampSigner 的 max_age 边界：age 等于 max_age 仍有效，超过上限或签名来自未来才过期，保留 return_timestamp 行为。',
  '''from itsdangerous import TimestampSigner, SignatureExpired
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
'''),
 ('its-base64', 'itsdangerous', edit('src/itsdangerous/encoding.py', 'string += b"=" * (-len(string) % 4)', 'string += b""'),
  '修复 URL-safe Base64 解码的填充回归，支持无填充字节和 ASCII 文本，并保持签名序列化往返可用。',
  '''from itsdangerous.encoding import base64_encode, base64_decode
from itsdangerous import URLSafeSerializer
for raw in [b'', b'a', b'ab', b'abc', bytes(range(128))]:
    token = base64_encode(raw)
    assert base64_decode(token) == raw
    assert base64_decode(token.decode('ascii')) == raw
s = URLSafeSerializer('fixture-key')
assert s.loads(s.dumps({'value': 'hello'})) == {'value': 'hello'}
'''),
 ('httpx-query-copy', 'httpx', edit('httpx/_urls.py', 'return list(self._dict.get(str(key), []))', 'return self._dict.get(str(key), [])'),
  '修复 QueryParams.get_list 返回内部可变列表的回归：调用者修改返回值不能影响原对象，重复值和缺失键保持兼容。',
  '''import httpx
q = httpx.QueryParams('a=1&a=2&b=3')
values = q.get_list('a'); values.append('bad')
assert q.get_list('a') == ['1','2'] and str(q) == 'a=1&a=2&b=3'
missing = q.get_list('absent'); missing.append('bad')
assert q.get_list('absent') == []
url = httpx.URL('https://example.test/?a=1&a=2')
url.params.get_list('a').clear()
assert url.params.get_list('a') == ['1','2']
'''),
 ('httpx-query-add', 'httpx', edit('httpx/_urls.py', 'q._dict[str(key)] = q.get_list(key) + [primitive_value_to_str(value)]', 'q._dict[str(key)] = q.get_list(key)[:0] + [primitive_value_to_str(value)]'),
  '修复 QueryParams.add 覆盖已有同名参数的问题；新对象应保留顺序、追加值并保持原对象不变，URL.copy_add_param 也应正确。',
  '''import httpx
q = httpx.QueryParams('a=1&a=2&b=3')
result = q.add('a', True)
assert result.get_list('a') == ['1','2','true']
assert q.get_list('a') == ['1','2']
assert result.get('b') == '3'
assert q.add('new', None).get('new') == ''
url = httpx.URL('https://example.test/?a=1').copy_add_param('a', 2)
assert url.params.get_list('a') == ['1','2']
'''),
]
for name, repo, mutation, prompt, checks in bugs:
    add('fix-' + name, repo, 'bugfix', prompt + ' 直接修改源码，不提交。', checks,
        input_edits=[mutation], oracle_edits=[edit(mutation['path'], mutation['after'], mutation['before'])])

# Four small, bounded API extensions with public integration checks.
add('feature-click-slug', 'click', 'feature',
    '在 click.utils 新增 command_slug(name: str)：去掉首尾空白，用连字符连接任意空白分隔的词，使用 Unicode casefold；从 click 顶层导出。空文本返回空字符串。',
    '''import click
assert click.command_slug('  Hello\\tWORLD ') == 'hello-world'
assert click.command_slug('Straße App') == 'strasse-app'
assert click.command_slug('   ') == ''
assert click.command_slug('Already-Dashed') == 'already-dashed'
''', oracle_edits=[edit('src/click/utils.py', 'def make_default_short_help(', 'def command_slug(name: str) -> str:\n    return "-".join(name.split()).casefold()\n\n\ndef make_default_short_help('),
                     edit('src/click/__init__.py', 'from .utils import echo as echo', 'from .utils import command_slug as command_slug\nfrom .utils import echo as echo')])
add('feature-click-positive', 'click', 'feature',
    '在 click.types 新增 PositiveIntParamType（name="positive-integer"），继承 IntParamType，接受可转为整数且 >0 的值，否则按 Click BadParameter 错误处理；从 click 顶层导出常量 POSITIVE_INT，支持 @click.option 使用。',
    '''import click
from click.testing import CliRunner
assert click.POSITIVE_INT.convert('3', None, None) == 3
for v in ['0', '-1', 'abc']:
    try: click.POSITIVE_INT.convert(v, None, None)
    except click.BadParameter: pass
    else: raise AssertionError('invalid positive integer')
@click.command()
@click.option('--count', type=click.POSITIVE_INT)
def cli(count): click.echo(count + 1)
assert CliRunner().invoke(cli, ['--count','4']).output.strip() == '5'
assert CliRunner().invoke(cli, ['--count','0']).exit_code == 2
''', oracle_edits=[edit('src/click/types.py', 'class IntRange(', 'class PositiveIntParamType(IntParamType):\n    name = "positive-integer"\n\n    def convert(self, value, param, ctx):\n        value = super().convert(value, param, ctx)\n        if value <= 0:\n            self.fail("must be positive", param, ctx)\n        return value\n\n\nPOSITIVE_INT = PositiveIntParamType()\n\n\nclass IntRange('),
                     edit('src/click/__init__.py', 'from .types import INT as INT', 'from .types import INT as INT\nfrom .types import POSITIVE_INT as POSITIVE_INT')])
add('feature-its-batch', 'itsdangerous', 'feature',
    '为 itsdangerous.Signer 增加 validate_many(values) 方法，接受任意可迭代的签名值，按输入顺序返回 bool 列表，复用已有 validate 行为，不因单个无效签名停止；空输入返回 []。',
    '''from itsdangerous import Signer
s = Signer('fixture-key')
assert s.validate_many(iter([s.sign('a'), b'bad', s.sign('b')])) == [True, False, True]
assert s.validate_many([]) == []
class Derived(Signer):
    def validate(self, value): return value == b'yes'
assert Derived('fixture-key').validate_many([b'yes', b'no']) == [True, False]
''', oracle_edits=[edit('src/itsdangerous/signer.py', '    def validate(self, signed_value:', '    def validate_many(self, values):\n        return [self.validate(value) for value in values]\n\n    def validate(self, signed_value:')])
add('feature-httpx-nonempty', 'httpx', 'feature',
    '为 httpx.QueryParams 增加 without_empty()：返回新 QueryParams，去除值为精确空字符串的项；重复参数中非空值保持顺序，空白字符串保留。不得修改原对象。',
    '''import httpx
q = httpx.QueryParams([('a',''),('a','1'),('a','2'),('b',''),('c',' ')])
r = q.without_empty()
assert r.multi_items() == [('a','1'),('a','2'),('c',' ')]
assert q.get_list('a') == ['', '1', '2']
assert str(httpx.QueryParams().without_empty()) == ''
assert r is not q
''', oracle_edits=[edit('httpx/_urls.py', '    def merge(self, params: QueryParamTypes', '    def without_empty(self) -> QueryParams:\n        return QueryParams([(key, value) for key, value in self.multi_items() if value != ""])\n\n    def merge(self, params: QueryParamTypes')])

# Two changed snapshots, two clean controls. Review labels are location labels.
for index in [2, 4]:
    name, repo, mutation, prompt, checks = bugs[index]
    source = (Path('test-results/benchmark-repos') / repo / mutation['path']).read_text(encoding='utf-8')
    line = source[:source.index(mutation['before'])].count('\n') + 1
    add('review-' + name, repo, 'review',
        '只读审查给出的 diff 与相关调用点。仅报告确定的回归，每个缺陷一次；禁止修改。最终输出 JSON 对象 {"findings":[{"file":"仓库相对路径","line":正整数,"description":"触发输入与影响"}]}。',
        checks, input_edits=[mutation], expected_findings=[{'file': mutation['path'], 'line': line}])
for repo, path in [('click','src/click/parser.py'), ('httpx','httpx/_status_codes.py')]:
    add('review-clean-' + repo, repo, 'review',
        f'只读审查 {path} 的现有行为与注释约定，仅报告确定的逻辑缺陷，不给风格建议。若无缺陷输出空 findings。输出 JSON 对象 {{"findings":[{{"file":"仓库相对路径","line":正整数,"description":"触发输入与影响"}}]}}。')

for index in [0, 2, 5]:
    name, repo, mutation, prompt, checks = bugs[index]
    add('testgen-' + name, repo, 'testgen',
        f'为当前正确实现补充 pytest 回归测试：{prompt} 只在 bench_tests/test_regression.py 写测试，不修改项目源码。不要依赖已有测试的 conftest；不要使用网络或真实时间等待。',
        mutants=[mutation], oracle_test='def test_regression():\n' + ''.join('    ' + line + '\n' for line in checks.splitlines()))

questions = [
 ('click', 'src/click/parser.py', 'normalize_opt', ['split_opt', 'token_normalize_func'],
  '选项名称的归一化如何保留前缀，什么配置钩子修改名称？请指出实现函数与条件。'),
 ('itsdangerous', 'src/itsdangerous/timed.py', 'class TimestampSigner', ['max_age', 'SignatureExpired'],
  '签名过期由哪个类检查？时间差等于 max_age 或为负时分别如何处理？'),
 ('httpx', 'httpx/_urls.py', '    def add(self, key:', ['QueryParams', 'get_list'],
  'QueryParams.add 如何追加同名参数而不覆盖原对象？说明返回对象和列表来源。'),
]
for repo, path, anchor, terms, prompt in questions:
    source = (Path('test-results/benchmark-repos') / repo / path).read_text(encoding='utf-8')
    line = source[:source.index(anchor)].count('\n') + 1
    add('qa-' + repo, repo, 'repoqa', prompt + ' 只读，输出 JSON 对象 {"answer":"说明","evidence":[{"path":"仓库相对路径","start_line":正整数,"end_line":正整数}]}，引用范围应覆盖相关定义，最多 200 行。',
        qa={'path': path, 'anchor_line': line, 'terms': terms})

Path('evals/repository-tasks.json').write_text(json.dumps({'version': 1, 'repositories': REPOS, 'tasks': TASKS}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'Authored {len(TASKS)} tasks')
