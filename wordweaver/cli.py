import os
import click
import pickle
import json

from flask.cli import FlaskGroup
from tqdm import tqdm

from wordweaver.app import app
from wordweaver import VERSION
from wordweaver.buildtools.swagger_spec_gen import SwaggerSpecGenerator
from wordweaver import __file__ as ww_file
from wordweaver.fst.utils.foma_access_python import foma_access_python as foma_access
from wordweaver.config import ENV_CONFIG
from wordweaver.log import logger
from wordweaver.resources.utils import return_plain
from wordweaver.data import pronoun_data, verb_data
from wordweaver.resources.affix import AFFIX_OPTIONS
from wordweaver.fst.encoder import FstEncoder
from wordweaver.fst.decoder import FstDecoder
from wordweaver.fst.english_generator import EnglishGenerator

DATA_DIR = os.environ.get('WW_DATA_DIR')

if not DATA_DIR:
    logger.warning(
        'WW_DATA_DIR environment variable is not set, using default sample data instead.')
    DATA_DIR = os.path.join(os.path.dirname(ww_file), 'sample', 'data')

FOMABINS_DIR = os.path.join(DATA_DIR, 'fomabins')


def create_app():
    return app


@click.version_option(version=VERSION, prog_name="wordweaver")
@click.group(cls=FlaskGroup, create_app=create_app)
def cli():
    '''Management script for WordWeaver'''


def dict_to_wordweaver_list(d, args):
    response = []
    for k, v in d.items():
        if k == 'affixes':
            for item in v:
                # Add Gloss
                item['gloss'] = item['tag']
                # Delete Tag
                del item['tag']
                # Turn type to list
                item['type'] = [item['type'], 'affix']
                response.append(item)
        if k == 'pronoun':
            # Add Gloss
            v['gloss'] = args['agent'] + ' > ' + args['patient']
            # Turn type fo list
            if args['root'][-2:] == '-r':
                pn_type = 'agent'
            elif args['root'][-2:] == '-b':
                pn_type = 'patient'
            else:
                pn_type = 'transitive'
            v['type'] = [pn_type, 'pronoun']
            response.append(v)
        if k == 'root':
            v['gloss'] = 'verb'
            v['type'] = ['root']
            response.append(v)
    return response


@click.option('--prune/--no-prune', default=False)
@cli.command()
def export(prune):
    ''' Exports data to JSON for WordWeaver 2.0 '''
    foma = foma_access(os.path.join(FOMABINS_DIR, ENV_CONFIG["fst_filename"]))
    verb_tags = [x['tag'] for x in verb_data]
    pronoun_tags = [x['tag'] for x in pronoun_data]
    option_tags = [x for x in AFFIX_OPTIONS['AFFIX_OPTIONS_TAGS']]
    args = {'root': verb_tags, 'agent': pronoun_tags,
            'patient': pronoun_tags, 'aff-option': option_tags}
    encoder = FstEncoder(args)
    translator = EnglishGenerator()
    tags = encoder.return_tags()
    logger.info(
        f'Created {len(tags)} tags from {len(verb_tags)} verb roots, {len(pronoun_tags)} pronouns and {len(option_tags)} options')
    result = []
    for tag in tqdm(tags):
        input_args = {'root': tag['http_args']['root'], 'agent': tag['http_args']['agent'],
                      'patient': tag['http_args']['patient'], 'option': tag['http_args']['affopt']}
        markers = list(foma.down(tag['fst']))
        if not markers and prune:
            continue
        elif not markers and not prune:
            result.append({'input': input_args, 'output': []})
            continue
        for marker in markers:
            decoder = FstDecoder(marker)
            output = dict_to_wordweaver_list(decoder.returnValuesFromMarkers(), tag['http_args'])
            translation = translator.transduce_tags(tag['fst'])
            output.append({'english': translation})
            result.append({'input': input_args,
                           'output': output})
    logger.info(f"Finished with {len(result)} results.")
    if prune:
        logger.info(f"Pruned f{len(result)-len(tags)} invalid inputs")
    with open('output.json', 'w') as f:
        f.write(json.dumps(result))


@cli.command()
def spec():
    ''' Update Swagger Specification
    '''
    gen = SwaggerSpecGenerator()
    gen.writeNewData()
    click.echo('Successfully updated Swagger Specification')


@click.option('--pkl/--no-pkl', default=False)
@click.option('--txt/--no-txt', default=False)
@click.option('--plain/--no-plain', default=False)
@click.argument('inp', type=click.STRING, default='')
@click.argument('command', type=click.Choice(['up', 'down', 'lower-words']))
@cli.command()
def foma(command, inp, plain, txt, pkl):
    ''' Interact with foma through command line
    '''
    foma = foma_access(os.path.join(FOMABINS_DIR, ENV_CONFIG["fst_filename"]))

    if command == 'up':
        res = [x for x in tqdm(foma.up(inp))]
    elif command == 'down':
        res = [x for x in tqdm(foma.down(inp))]
    elif command == 'lower-words':
        res = [x for x in tqdm(foma.lower_words())]

    if plain:
        click.echo('Removing Markup')
        for i, x in tqdm(enumerate(res)):
            res[i] = return_plain(x)

    click.echo(res)

    if pkl:
        with open('res.pkl', 'wb') as f:
            pickle.dump(res, f)
        click.echo('Wrote FST response to pickle')

    if txt:
        with open('res.txt', 'w') as f:
            f.writelines('\n'.join(res))
        click.echo('Wrote FST response to text')
