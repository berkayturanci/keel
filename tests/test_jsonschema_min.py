"""Unit tests for the dependency-free JSON-Schema subset validator."""

import unittest

from keel import jsonschema_min as js


class TestType(unittest.TestCase):
    def test_accepts_correct_types(self):
        for value, t in [("x", "string"), (1, "integer"), (1.5, "number"),
                         (True, "boolean"), ([], "array"), ({}, "object"), (None, "null")]:
            self.assertEqual(js.validate(value, {"type": t}), [], f"{value!r} as {t}")

    def test_rejects_wrong_type(self):
        errs = js.validate("x", {"type": "integer"})
        self.assertEqual(len(errs), 1)
        self.assertIn("expected type integer", errs[0])

    def test_bool_is_not_integer_or_number(self):
        self.assertTrue(js.validate(True, {"type": "integer"}))
        self.assertTrue(js.validate(True, {"type": "number"}))

    def test_int_is_accepted_as_number(self):
        self.assertEqual(js.validate(3, {"type": "number"}), [])

    def test_type_list(self):
        schema = {"type": ["string", "null"]}
        self.assertEqual(js.validate("x", schema), [])
        self.assertEqual(js.validate(None, schema), [])
        self.assertTrue(js.validate(5, schema))

    def test_unknown_type_name_not_enforced(self):
        self.assertEqual(js.validate("anything", {"type": "weird"}), [])

    def test_wrong_type_stops_deeper_checks(self):
        # pattern would also fail, but we should report only the type error.
        errs = js.validate(5, {"type": "string", "pattern": "^a$"})
        self.assertEqual(len(errs), 1)
        self.assertIn("expected type string", errs[0])


class TestConstEnum(unittest.TestCase):
    def test_const_ok_and_bad(self):
        self.assertEqual(js.validate("keel", {"const": "keel"}), [])
        self.assertTrue(js.validate("nope", {"const": "keel"}))

    def test_enum(self):
        schema = {"enum": ["a", "b"]}
        self.assertEqual(js.validate("a", schema), [])
        self.assertIn("must be one of", js.validate("c", schema)[0])


class TestString(unittest.TestCase):
    def test_pattern(self):
        schema = {"type": "string", "pattern": "^[0-9]{2}:[0-9]{2}$"}
        self.assertEqual(js.validate("07:30", schema), [])
        self.assertIn("does not match pattern", js.validate("7:30", schema)[0])

    def test_min_length(self):
        schema = {"type": "string", "minLength": 2}
        self.assertEqual(js.validate("ab", schema), [])
        self.assertIn("minLength", js.validate("a", schema)[0])


class TestArray(unittest.TestCase):
    def test_items(self):
        schema = {"type": "array", "items": {"type": "string"}}
        self.assertEqual(js.validate(["a", "b"], schema), [])
        errs = js.validate(["a", 2], schema)
        self.assertEqual(len(errs), 1)
        self.assertIn("[1]", errs[0])

    def test_min_items(self):
        schema = {"type": "array", "minItems": 1}
        self.assertEqual(js.validate(["a"], schema), [])
        self.assertIn("minItems", js.validate([], schema)[0])

    def test_items_non_dict_ignored(self):
        # A non-dict "items" (e.g. tuple-form, unsupported) is simply ignored.
        self.assertEqual(js.validate([1, 2], {"type": "array", "items": True}), [])


class TestObject(unittest.TestCase):
    def test_required(self):
        schema = {"type": "object", "required": ["a"]}
        self.assertEqual(js.validate({"a": 1}, schema), [])
        self.assertIn("missing required property 'a'", js.validate({}, schema)[0])

    def test_properties_recurse(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        self.assertEqual(js.validate({"a": "x"}, schema), [])
        errs = js.validate({"a": 1}, schema)
        self.assertIn("$.a", errs[0])

    def test_additional_properties_false(self):
        schema = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
        self.assertEqual(js.validate({"a": 1}, schema), [])
        self.assertIn("unknown property 'b'", js.validate({"a": 1, "b": 2}, schema)[0])

    def test_additional_properties_schema(self):
        # 'a' is a declared property (validated by it, skipped by additionalProperties);
        # 'x' is additional and must satisfy the additionalProperties schema.
        schema = {
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "additionalProperties": {"type": "string"},
        }
        self.assertEqual(js.validate({"a": 1, "x": "ok"}, schema), [])
        self.assertIn("$.x", js.validate({"a": 1, "x": 5}, schema)[0])

    def test_nested_path_rendering(self):
        schema = {
            "type": "object",
            "properties": {"k": {"type": "object", "properties": {"v": {"type": "string"}}}},
        }
        errs = js.validate({"k": {"v": 1}}, schema)
        self.assertIn("$.k.v", errs[0])


class TestKind(unittest.TestCase):
    """The 'got <kind>' rendering used in type-mismatch messages."""

    def test_kind_messages(self):
        self.assertIn("got integer", js.validate(5, {"type": "string"})[0])
        self.assertIn("got number", js.validate(1.5, {"type": "string"})[0])
        self.assertIn("got null", js.validate(None, {"type": "string"})[0])
        self.assertIn("got boolean", js.validate(True, {"type": "string"})[0])
        self.assertIn("got object", js.validate({}, {"type": "string"})[0])
        self.assertIn("got array", js.validate([], {"type": "string"})[0])

    def test_kind_fallback_to_type_name(self):
        self.assertIn("got set", js.validate({1, 2}, {"type": "string"})[0])


class TestNonDictSchema(unittest.TestCase):
    def test_non_dict_schema_is_noop(self):
        self.assertEqual(js.validate("anything", True), [])


if __name__ == "__main__":
    unittest.main()
