import click
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

