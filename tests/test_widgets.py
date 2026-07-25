"""Tests for elidia.widgets — protocol and validation."""
import pytest

from elidia.widgets.protocol import (
    WidgetResponse,
    WidgetType,
    create_confirm_widget,
    create_form_widget,
    create_mcq_widget,
    create_select_widget,
    create_text_widget,
    validate_response,
)


class TestCreateWidgets:
    def test_text_widget(self):
        w = create_text_widget("t1", "Name", default="J", placeholder="Enter name")
        assert w.type == WidgetType.TEXT
        assert w.id == "t1"
        assert w.title == "Name"
        assert w.default == "J"
        assert len(w.fields) == 1

    def test_select_widget(self):
        w = create_select_widget("s1", "Pick", [("Alpha", "a"), ("Beta", "b")])
        assert w.type == WidgetType.SELECT
        assert len(w.options) == 2
        assert w.options[0].label == "Alpha"
        assert w.options[0].value == "a"

    def test_confirm_widget(self):
        w = create_confirm_widget("c1", "OK?", default="yes")
        assert w.type == WidgetType.CONFIRM
        assert w.default == "yes"
        assert len(w.options) == 2

    def test_mcq_widget(self):
        w = create_mcq_widget("m1", "Select many", [("X", "x"), ("Y", "y"), ("Z", "z")])
        assert w.type == WidgetType.MCQ
        assert len(w.options) == 3

    def test_form_widget_text_field(self):
        w = create_form_widget("f1", "Form", [
            {"name": "email", "label": "Email", "required": True},
        ])
        assert w.type == WidgetType.FORM
        assert len(w.fields) == 1
        assert w.fields[0].name == "email"
        assert w.fields[0].required

    def test_form_widget_select_field(self):
        w = create_form_widget("f2", "Form", [
            {
                "name": "color",
                "label": "Color",
                "type": "select",
                "options": [("Red", "red"), ("Blue", "blue")],
            },
        ])
        assert w.fields[0].type == WidgetType.SELECT
        assert len(w.fields[0].options) == 2


class TestValidateResponse:
    def test_required_text_missing(self):
        w = create_text_widget("t1", "Name", required=True)
        resp = WidgetResponse(id="t1", values={"value": ""})
        errors = validate_response(w, resp)
        assert len(errors) >= 1

    def test_required_text_present(self):
        w = create_text_widget("t1", "Name", required=True)
        resp = WidgetResponse(id="t1", values={"value": "Alice"})
        errors = validate_response(w, resp)
        assert len(errors) == 0

    def test_cancelled_always_valid(self):
        w = create_text_widget("t1", "Name", required=True)
        resp = WidgetResponse(id="t1", cancelled=True)
        errors = validate_response(w, resp)
        assert len(errors) == 0

    def test_select_valid_option(self):
        w = create_select_widget("s1", "Pick", [("A", "a"), ("B", "b")])
        resp = WidgetResponse(id="s1", values={"value": "a"})
        errors = validate_response(w, resp)
        assert len(errors) == 0

    def test_select_invalid_option(self):
        w = create_select_widget("s1", "Pick", [("A", "a"), ("B", "b")])
        resp = WidgetResponse(id="s1", values={"value": "c"})
        errors = validate_response(w, resp)
        assert len(errors) >= 1

    def test_form_required_field_missing(self):
        w = create_form_widget("f1", "Form", [
            {"name": "email", "label": "Email", "required": True},
            {"name": "phone", "label": "Phone"},
        ])
        resp = WidgetResponse(id="f1", values={"phone": "123"})
        errors = validate_response(w, resp)
        assert len(errors) >= 1

    def test_form_all_required_present(self):
        w = create_form_widget("f1", "Form", [
            {"name": "email", "label": "Email", "required": True},
        ])
        resp = WidgetResponse(id="f1", values={"email": "a@b.com"})
        errors = validate_response(w, resp)
        assert len(errors) == 0
