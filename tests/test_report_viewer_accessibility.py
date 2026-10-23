from html.parser import HTMLParser
import re

import pytest

from test_report_presentation import GENERATOR, load_generator, write_fixture


class ViewerElements(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.elements = {}
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        key = attributes.get("id") or attributes.get("class")
        if key:
            self.elements[key] = (tag, attributes)


@pytest.mark.parametrize("meta", [False, True])
def test_figure_viewer_has_named_scroll_region_and_explicit_zoom_controls(tmp_path, meta):
    write_fixture(tmp_path, "counts")
    generator = GENERATOR.with_name("make_meta_report.py") if meta else GENERATOR
    elements = ViewerElements(load_generator(generator).build(tmp_path)).elements
    _, stage = elements["lb-stage"]
    assert stage.get("role") == "region"
    assert stage.get("aria-label") == "Scrollable figure"
    assert stage.get("tabindex") == "0"
    tag, image = elements["bsq-lb-img"]
    assert tag == "img" and "alt" in image
    assert "tabindex" not in image and "role" not in image
    for control in ("fit", "actual", "in", "out", "close"):
        tag, attributes = elements[f"bsq-lb-{control}"]
        assert tag == "button" and attributes.get("type") == "button"


def test_viewer_image_does_not_offer_an_unnamed_zoom_action():
    module = load_generator()
    assert "li.addEventListener('click'" not in module.FIGURE_DIALOG_SCRIPT
    assert "li.addEventListener('keydown'" not in module.FIGURE_DIALOG_SCRIPT
    viewer_css = re.findall(r"\.lb img(?:\.zoomed)?\{([^}]*)\}", module.CSS)
    assert viewer_css
    assert all("cursor:zoom-" not in rule for rule in viewer_css)
