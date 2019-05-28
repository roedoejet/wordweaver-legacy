# -*- coding: utf-8 -*-
from flask import jsonify, Blueprint, abort, send_file, send_from_directory, after_this_request
import json
import os.path
import re
from tempfile import NamedTemporaryFile, TemporaryFile, mkstemp

from flask_restful import (Resource, Api, reqparse, inputs, fields, url_for, marshal_with, marshal)
from flask_cors import CORS
from slugify import slugify

from wordweaver import app

if app.config['FOMA_PYTHON_BINDINGS']:
    from wordweaver.fst.utils.foma_access_python import foma_access_python as foma_access
else:
    from wordweaver.fst.utils.foma_access import foma_access
    
from wordweaver.fst.decoder import FstDecoder
from wordweaver.fst.encoder import FstEncoder
from wordweaver.exceptions import FomaInputException
from wordweaver.fst.english_generator import EnglishGenerator
from wordweaver.resources import require_appkey
from wordweaver.data.api_data.models import verb_data
from wordweaver.buildtools.file_maker import DocxMaker, LatexMaker

from wordweaver.resources.affix import affix_fields
from wordweaver.resources.pronoun import pronoun_fields
from wordweaver.resources.verb import verb_fields

import itertools

from wordweaver.configs import ENV_CONFIG
from wordweaver.data import fomabins as fomabins_dir



conjugation_fields = {
    'root': fields.Nested(verb_fields),
    'aspect': fields.Nested(affix_fields),
    'agent': fields.Nested(pronoun_fields),
    'patient': fields.Nested(pronoun_fields),
    'tmp_affix': fields.Nested(affix_fields),
    'pas': fields.Nested(affix_fields)
}

class ConjugationList(Resource):
    def __init__(self, fp=foma_access(os.path.join(os.path.dirname(fomabins_dir.__file__),
                               ENV_CONFIG['fst_filename']))):
        print(fp.path_to_model)
        self.parser = reqparse.RequestParser()
        self.fp = fp
        self.eg = EnglishGenerator()

        self.parser.add_argument(
            'agent', dest='agent',
            type=str, location='args', action='append',
            required=False, help='An agent tag for the conjugation',
        )

        self.parser.add_argument(
            'patient', dest='patient',
            type=str, location='args', action='append',
            required=False, help='A patient tag for the conjugation',
        )

        self.parser.add_argument(
            'aff-option', dest='aff-option',
            type=str, location='args', action='append',
            required=False, help='An affix option tag for the conjugation',
        )

        self.parser.add_argument(
            'root', dest='root',
            type=str, location='args', action='append',
            required=False, help='A verb root tag for the conjugation',
        )

        self.parser.add_argument(
            'offset', dest='offset',
            type=int, location='args', default=0, 
            required=False, help='An offset for conjugations with default of 0 - maximum range between offset and limit is 100',
        )

        self.parser.add_argument(
            'limit', dest='limit',
            type=int, location='args', default=5,
            required=False, help='A limit for conjugations with a default of 5 - maximum range between offset and limit is 100',
        )

        self.parser.add_argument(
            'markers', dest='markers',
            type=bool, location='args', default=False,
            required=False, help='Return the marker generated by the FST instead of the normal response. Meant for debugging.',
        )

        self.parser.add_argument(
            'tags', dest='tags',
            type=bool, location='args', default=False,
            required=False, help='Return the tag given to the FST instead of the normal response. Meant for debugging',
        )

        self.parser.add_argument(
            'plain', dest='plain',
            type=bool, location='args', default=False,
            required=False, help='Return plain text. Meant for debugging',
        )

        self.parser.add_argument(
            'docx', dest='docx',
            type=bool, location='args', default=False,
            required=False, help='Return docx file.',
        )

        self.parser.add_argument(
            'latex', dest='latex',
            type=bool, location='args', default=False,
            required=False, help='Return latex file.',
        )
  
    def mergeTagsAndValues(self, tags, values):
        new_values = []
        for counter, value in enumerate(values):
            value['root']['tag'] = tags[counter]['root']
            value['pronoun']['agent'] = ''
            value['pronoun']['patient'] = ''
            value['pronoun']['agent'] = tags[counter]['agent']
            value['pronoun']['patient'] = tags[counter]['patient']
            value['affopt'] = tags[counter]['affopt']
            new_values.append(value)
        return new_values

    def returnPlain(self, marker):
        vals_pattern = re.compile(r"\^[A-Z][\w\-\']*\^")
        values = re.split(vals_pattern, marker)
        new_value = [x for x in values if x]
        return "-".join(new_value)

    @require_appkey
    def get(self):
        # Get args
        args = self.parser.parse_args()
        conj_range = args['limit'] - args['offset']
        if conj_range > 10:
            abort(403, description = "Range between offset and limit {} exceeds maximum allowed. Please contact developers if you require more access.".format(conj_range))
  
        # Turn args into tags
        tag_maker = FstEncoder(args)
        try:
            tags = tag_maker.return_tags()
        except FomaInputException as e:
            print(("Foma Error" + e))
            abort (400, description = "The FST failed to conjugate. Exception: {}".format(str(e)) )
       
        if 'tags' in args and args['tags']:
            # Add index for reference
            tags = [(i, tags[i][x], tags[i][y]) for i, (x, y) in enumerate(tags)]
            return tags

        eng_translations = [self.eg.transduce_tags(x['fst']) for x in tags]
        
        # Trigger "down" from fst with tags (return verb). foma bindings return generator, so next must be used there.
        fst_tags = [x['fst'] for x in tags]
        markers = []
        for tag in fst_tags:
            try:
                markers.append(list(self.fp.down(tag))[0])
            except IndexError:
                markers.append("???")

        if 'markers' in args and args['markers']:
            # Add index for reference
            markers = [{'index': i, 'marker': m} for i, m in enumerate(markers)]
            return markers

        if 'plain' in args and args['plain']:
            plain = [{'index': i, 'text': self.returnPlain(m)} for i, m in enumerate(markers)]
            return plain

         # This is interim and not good. list comprehension to remove ??? and + in markers and corresponding tags. This should make both same length
        filtered_markers = [x != "???" and "+" not in x for x in markers]
        tags = list(itertools.compress(tags, filtered_markers))
        markers = list(itertools.compress(markers, filtered_markers))
        eng_translations = list(itertools.compress(eng_translations, filtered_markers))

        # return markers
      
        if "???" in markers:
            abort(400, description="The FST could not conjugate the tag: {} or the marker: {}.".format(tags, markers))

        # return markers
        # "Decode" markers into HTTP response
        response = []
        for marker in markers:
            decoder = FstDecoder(marker)
            values = decoder.returnValuesFromMarkers()
            response.append(values)
        self.mergeTagsAndValues([x['http_args'] for x in tags], response)

        for counter, entry in enumerate(response):
            entry['translation'] = eng_translations[counter]

        if "docx" in args and args['docx']:
            dm = DocxMaker(response)
            document = dm.export()
            fd, path = mkstemp()
            try:
                document.save(path)
                return send_file(path,
                                as_attachment=True,
                                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                attachment_filename='conjugations.docx')
            finally:
                os.remove(path)
            
        if "latex" in args and args['latex']:
            lm = LatexMaker(response)
            latex = lm.export_to_bytes()
            fd, path = mkstemp()
            try:
                with os.fdopen(fd, 'wb') as tmp:
                    # do stuff with temp file
                    tmp.write(latex)
                    tmp.seek(0)
                    return send_file(path,
                                    as_attachment=True,
                                    mimetype='text/plain',
                                    attachment_filename='conjugations.tex')
            finally:
                os.remove(path)

        return response

class ConjugationVerbList(ConjugationList):
    def __init__(self, fp):
        print(fp.path_to_model)
        super().__init__(fp)
    @require_appkey
    def get(self, root):
        try:
            next(vb for vb in verb_data if vb['tag'] == slugify(root))
        except StopIteration:
            abort(404)
            
        args = self.parser.parse_args()

        args['root'] = [root]
        # Turn args into tags
        tag_maker = FstEncoder(args)
        
        try:
            tags = tag_maker.return_tags()

        except FomaInputException as e:
            print(("Foma Error" + e))
            abort (400, description = "The FST failed to conjugate. Exception: {}".format(str(e)) )
        
        if 'tags' in args and args['tags']:
            # Add index for reference
            tags = [(i, tags[i][x], tags[i][y]) for i, (x, y) in enumerate(tags)]
            return tags

        eng_translations = [self.eg.transduce_tags(x['fst']) for x in tags]

        # Trigger "down" from fst with tags (return verb). foma bindings return generator, so next must be used there.
        fst_tags = [x['fst'] for x in tags]
        
        markers = []
        for tag in fst_tags:
            try:
                markers.append(list(self.fp.down(tag))[0])
            except IndexError:
                markers.append("???")

        if 'markers' in args and args['markers']:
            # Add index for reference
            markers = [{'index': i, 'marker': m} for i, m in enumerate(markers)]
            return markers

        if 'plain' in args and args['plain']:
            plain = [{'index': i, 'text': self.returnPlain(m)} for i, m in enumerate(markers)]
            return plain

        # This is interim and not good. list comprehension to remove ??? and + in markers and corresponding tags. This should make both same length
        filtered_markers = [x != "???" and "+" not in x for x in markers]
        tags = list(itertools.compress(tags, filtered_markers))
        markers = list(itertools.compress(markers, filtered_markers))
        eng_translations = list(itertools.compress(eng_translations, filtered_markers))

        if len(tags) != len(markers):
            abort(500, description="Whoa, something went wrong. The tag and marker lists are not equal in length")

        # return markers

        if "???" in markers:
            abort(400, description="The FST could not conjugate the tag: {} or the marker: {}.".format(tags, markers))

        response = []

        for marker in markers:
            decoder = FstDecoder(marker)
            values = decoder.returnValuesFromMarkers()
            response.append(values)

        self.mergeTagsAndValues([x['http_args'] for x in tags], response)

        for counter, entry in enumerate(response):
            entry['translation'] = eng_translations[counter]
        
        if "docx" in args and args['docx']:
            dm = DocxMaker(response)
            document = dm.export()
            fd, path = mkstemp()
            try:
                document.save(path)
                return send_file(path,
                                as_attachment=True,
                                mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                attachment_filename='test.docx')
            finally:
                os.remove(path)
            
        if "latex" in args and args['latex']:
            lm = LatexMaker(response)
            latex = lm.export_to_bytes()
            fd, path = mkstemp()
            try:
                with os.fdopen(fd, 'wb') as tmp:
                    # do stuff with temp file
                    tmp.write(latex)
                    tmp.seek(0)
                    return send_file(path,
                                    as_attachment=True,
                                    mimetype='text/plain',
                                    attachment_filename='test.tex')
            finally:
                os.remove(path)

        return response

## Main API

conjugation_api = Blueprint('resources.conjugation', __name__)

CORS(conjugation_api)

api = Api(conjugation_api)

api.add_resource(
    ConjugationList,
    '/conjugations',
    endpoint='conjugations'
)

api.add_resource(
    ConjugationVerbList,
    '/conjugations/<string:verb>',
    endpoint='conjugations/verb'
)

## Secondary API

conjugation_api_2 = Blueprint('resources.conjugation2', __name__)

CORS(conjugation_api_2)

api2 = Api(conjugation_api_2)

api2.add_resource(
    ConjugationList,
    '/conjugations',
    endpoint='conjugations', 
    resource_class_kwargs={'fp': foma_access(os.path.join(os.path.dirname(fomabins_dir.__file__),
                           ENV_CONFIG['test_fst_filename']))}
)

api2.add_resource(
    ConjugationVerbList,
    '/conjugations/<string:verb>',
    endpoint='conjugations/verb', 
    resource_class_kwargs={'fp': foma_access(os.path.join(os.path.dirname(fomabins_dir.__file__),
                           ENV_CONFIG['test_fst_filename']))}
)