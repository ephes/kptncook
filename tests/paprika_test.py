import os
import pytest

from jinja2.exceptions import TemplateNotFound

from kptncook.paprika import PaprikaExporter, ExportRenderer
from kptncook.models import Recipe


def test_asciify_string():
    p = PaprikaExporter()
    assert p.asciify_string("Süßkartoffeln mit Taboulé & Dip") == "Susskartoffeln_mit_Taboule___Dip"
    assert p.asciify_string("Ölige_Ähren") == "Olige_Ahren"


def test_get_cover_img_as_base64_string(full_recipe):
    p = PaprikaExporter()
    recipe = Recipe.parse_obj(full_recipe)
    cover_info = p.get_cover_img_as_base64_string(recipe=recipe)
    assert isinstance(cover_info, tuple) is True
    assert len(cover_info) == 2

    # no images availlable for some reason
    recipe.image_list = list()
    cover_info = p.get_cover_img_as_base64_string(recipe=recipe)
    assert cover_info is None


def test_export(full_recipe):
    p = PaprikaExporter()
    recipe = Recipe.parse_obj(full_recipe)
    p.export(recipe=recipe)
    expected_file = "Uberbackene_Muschelnudeln_mit_Lachs___Senf_Dill_Sauce.paprikarecipes"
    assert os.path.isfile(expected_file) is True
    if os.path.isfile(expected_file):
        os.unlink(expected_file)


def test_get_cover(minimal):
    p = PaprikaExporter()
    recipe = Recipe.parse_obj(minimal)
    assert p.get_cover(image_list=list()) is None

    cover = p.get_cover(image_list=recipe.image_list)
    assert cover.name == 'REZ_1837_Cover.jpg'

    with pytest.raises(ValueError):
        cover = p.get_cover(image_list=None)

    with pytest.raises(ValueError):
        cover = p.get_cover(image_list=dict())


def test_get_template_dir():
    r = ExportRenderer()
    assert os.path.isdir(r.get_template_dir()) is True


def test_render(minimal):
    # happy path
    recipe = Recipe.parse_obj(minimal)
    r = ExportRenderer()
    json = r.render(template_name="paprika.jinja2.json", recipe=recipe)
    assert json == ('{\n'
                    '   "uid":"",\n'
                    '   "name":"Minimal Recipe",\n'
                    '   "directions": "Alles parat?\\n",\n'
                    '   "servings":"2",\n'
                    '   "rating":0,\n'
                    '   "difficulty":"",\n'
                    '   "ingredients":"",\n'
                    '   "notes":"",\n'
                    '   "created":"",\n'
                    '   "image_url":null,\n'
                    '   "cook_time":"",\n'
                    '   "prep_time":"20",\n'
                    '   "source":"Kptncook",\n'
                    '   "source_url":"",\n'
                    '   "hash" : "",\n'
                    '   "photo_hash":null,\n'
                    '   "photos":[],\n'
                    '   "photo": "",\n'
                    '   "nutritional_info":"calories: 100\\nprotein: 30\\nfat: 10\\ncarbohydrate: '
                    '20\\n",\n'
                    '   "photo_data":"",\n'
                    '   "photo_large":null,\n'
                    '   "categories":["Kptncook"]\n'
                    '}')
    # invalid
    with pytest.raises(TemplateNotFound):
        json = r.render(template_name="invalid_template.jinja2.json", recipe=recipe)
