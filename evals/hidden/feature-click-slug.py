import click
assert click.command_slug('  Hello\tWORLD ') == 'hello-world'
assert click.command_slug('Straße App') == 'strasse-app'
assert click.command_slug('   ') == ''
assert click.command_slug('Already-Dashed') == 'already-dashed'

