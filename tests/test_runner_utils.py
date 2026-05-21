"""Tests for runner_utils helpers."""
from __future__ import annotations

import math

from core.runner_utils import is_blank_dataset_row


def test_blank_row_all_empty_strings():
    assert is_blank_dataset_row({"First Name": "", "Email": ""}) is True


def test_blank_row_all_none():
    assert is_blank_dataset_row({"First Name": None, "Email": None}) is True


def test_blank_row_pandas_nan():
    # data_editor.to_dict(orient="records") emits float('nan') for cells the
    # user never touched when the underlying DataFrame had mixed columns.
    assert is_blank_dataset_row({"First Name": math.nan, "Email": math.nan}) is True


def test_blank_row_whitespace_only():
    assert is_blank_dataset_row({"First Name": "   ", "Email": "\t"}) is True


def test_blank_row_metadata_only():
    # An __expected_outcome marker alone doesn't make a row runnable.
    assert is_blank_dataset_row({"__expected_outcome": "success"}) is True


def test_non_blank_row_one_value():
    assert is_blank_dataset_row({"First Name": "John", "Email": ""}) is False


def test_non_blank_row_zero_value():
    # "0" is a real value (e.g. min boundary), not blank.
    assert is_blank_dataset_row({"Age": "0"}) is False


def test_non_blank_row_with_metadata():
    assert is_blank_dataset_row(
        {"First Name": "John", "__expected_outcome": "success"}
    ) is False


def test_empty_dict():
    assert is_blank_dataset_row({}) is True
