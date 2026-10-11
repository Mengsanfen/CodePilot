import click
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

