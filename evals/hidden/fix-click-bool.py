import click
from click.testing import CliRunner
assert click.BOOL.convert('  YES  ', None, None) is True
assert click.BOOL.convert(' OFF\t', None, None) is False
assert click.BOOL.convert(True, None, None) is True
try: click.BOOL.convert('maybe', None, None)
except click.BadParameter: pass
else: raise AssertionError('invalid boolean accepted')
@click.command()
@click.option('--enabled', type=click.BOOL)
def cli(enabled): click.echo(str(enabled))
result = CliRunner().invoke(cli, ['--enabled', ' true '])
assert result.exit_code == 0 and result.output.strip() == 'True'

