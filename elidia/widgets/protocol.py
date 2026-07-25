"""Widget protocol — defines structured input collection for agents.

When an agent needs structured input from the user (model selection,
parameter configuration, confirmations), it emits a WidgetRequest.
The CLI renderer converts it to interactive prompt_toolkit widgets.

Widget types:
  text      — single or multi-line text input
  select    — dropdown / list selection (single choice)
  confirm   — yes/no confirmation
  form      — multi-field grouped inputs
  mcq       — multiple choice (checkboxes)
  password  — masked text input
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class WidgetType(str, Enum):
    TEXT = "text"
    SELECT = "select"
    CONFIRM = "confirm"
    FORM = "form"
    MCQ = "mcq"
    PASSWORD = "password"


@dataclass
class WidgetOption:
    label: str
    value: str
    description: str = ""
    disabled: bool = False


@dataclass
class WidgetField:
    name: str
    label: str
    type: WidgetType = WidgetType.TEXT
    required: bool = False
    default: str = ""
    placeholder: str = ""
    options: list[WidgetOption] = field(default_factory=list)
    validation: str = ""
    multiline: bool = False


@dataclass
class WidgetRequest:
    id: str
    type: WidgetType
    title: str
    description: str = ""
    fields: list[WidgetField] = field(default_factory=list)
    options: list[WidgetOption] = field(default_factory=list)
    default: str = ""
    required: bool = True
    timeout_seconds: int = 0


@dataclass
class WidgetResponse:
    id: str
    values: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False


def create_text_widget(
    widget_id: str,
    title: str,
    description: str = "",
    default: str = "",
    placeholder: str = "",
    multiline: bool = False,
    required: bool = True,
) -> WidgetRequest:
    logger.debug(f"Entered into create_text_widget: id={widget_id}")
    return WidgetRequest(
        id=widget_id,
        type=WidgetType.TEXT,
        title=title,
        description=description,
        default=default,
        required=required,
        fields=[WidgetField(
            name="value",
            label=title,
            type=WidgetType.TEXT,
            default=default,
            placeholder=placeholder,
            multiline=multiline,
            required=required,
        )],
    )


def create_select_widget(
    widget_id: str,
    title: str,
    options: list[tuple[str, str]],
    description: str = "",
    default: str = "",
) -> WidgetRequest:
    logger.debug(f"Entered into create_select_widget: id={widget_id}, options={len(options)}")
    return WidgetRequest(
        id=widget_id,
        type=WidgetType.SELECT,
        title=title,
        description=description,
        default=default,
        options=[WidgetOption(label=label, value=value) for label, value in options],
    )


def create_confirm_widget(
    widget_id: str,
    title: str,
    description: str = "",
    default: str = "no",
) -> WidgetRequest:
    logger.debug(f"Entered into create_confirm_widget: id={widget_id}")
    return WidgetRequest(
        id=widget_id,
        type=WidgetType.CONFIRM,
        title=title,
        description=description,
        default=default,
        options=[
            WidgetOption(label="Yes", value="yes"),
            WidgetOption(label="No", value="no"),
        ],
    )


def create_mcq_widget(
    widget_id: str,
    title: str,
    options: list[tuple[str, str]],
    description: str = "",
    min_selections: int = 0,
    max_selections: int = 0,
) -> WidgetRequest:
    logger.debug(f"Entered into create_mcq_widget: id={widget_id}, options={len(options)}")
    return WidgetRequest(
        id=widget_id,
        type=WidgetType.MCQ,
        title=title,
        description=description,
        options=[WidgetOption(label=label, value=value) for label, value in options],
        fields=[WidgetField(
            name="selections",
            label=title,
            type=WidgetType.MCQ,
            validation=f"min:{min_selections},max:{max_selections}" if min_selections or max_selections else "",
        )],
    )


def create_form_widget(
    widget_id: str,
    title: str,
    fields: list[dict[str, Any]],
    description: str = "",
) -> WidgetRequest:
    logger.debug(f"Entered into create_form_widget: id={widget_id}, fields={len(fields)}")
    parsed_fields: list[WidgetField] = []
    for f in fields:
        field_type = WidgetType(f.get("type", "text"))
        options: list[WidgetOption] = []
        if "options" in f:
            for opt in f["options"]:
                if isinstance(opt, tuple) and len(opt) >= 2:
                    options.append(WidgetOption(label=opt[0], value=opt[1]))
                elif isinstance(opt, dict):
                    options.append(WidgetOption(
                        label=opt.get("label", ""),
                        value=opt.get("value", ""),
                        description=opt.get("description", ""),
                    ))
                elif isinstance(opt, str):
                    options.append(WidgetOption(label=opt, value=opt))

        parsed_fields.append(WidgetField(
            name=f.get("name", ""),
            label=f.get("label", f.get("name", "")),
            type=field_type,
            required=f.get("required", False),
            default=str(f.get("default", "")),
            placeholder=f.get("placeholder", ""),
            options=options,
            validation=f.get("validation", ""),
            multiline=f.get("multiline", False),
        ))

    return WidgetRequest(
        id=widget_id,
        type=WidgetType.FORM,
        title=title,
        description=description,
        fields=parsed_fields,
    )


def validate_response(request: WidgetRequest, response: WidgetResponse) -> list[str]:
    logger.debug(f"Entered into validate_response: id={request.id}")
    errors: list[str] = []

    if response.cancelled:
        return errors

    if request.type == WidgetType.FORM:
        for f in request.fields:
            if f.required:
                val = response.values.get(f.name, "")
                if not val and val != 0:
                    errors.append(f"Field '{f.label}' is required")

            if f.options and f.name in response.values:
                valid_values = {o.value for o in f.options if not o.disabled}
                val = response.values[f.name]
                if isinstance(val, list):
                    for v in val:
                        if v not in valid_values:
                            errors.append(f"Invalid selection '{v}' for '{f.label}'")
                elif val and val not in valid_values:
                    errors.append(f"Invalid selection '{val}' for '{f.label}'")

    elif request.type == WidgetType.SELECT:
        val = response.values.get("value", "")
        if request.required and not val:
            errors.append(f"Selection required for '{request.title}'")
        if val and request.options:
            valid = {o.value for o in request.options if not o.disabled}
            if val not in valid:
                errors.append(f"Invalid selection: '{val}'")

    elif request.type == WidgetType.TEXT:
        if request.required and not response.values.get("value", ""):
            errors.append(f"Text input required for '{request.title}'")

    return errors
