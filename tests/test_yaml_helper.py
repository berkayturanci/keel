import builtins
import unittest
from importlib import reload
from unittest.mock import patch


class TestYamlHelper(unittest.TestCase):
    def test_c_extensions_used(self):
        # Verify the C extensions are mapped (since we assume they are available in this env)
        import yaml

        from keel import yaml_helper

        self.assertIs(yaml_helper._SafeLoader, yaml.CSafeLoader)
        self.assertIs(yaml_helper._SafeDumper, yaml.CSafeDumper)

        # Test basic functionality
        data = "foo: bar"
        parsed = yaml_helper.load(data)
        self.assertEqual(parsed, {"foo": "bar"})

        dumped = yaml_helper.dump(parsed)
        self.assertIn("foo: bar", dumped)

    def test_pure_python_fallback(self):
        import yaml

        original_import = builtins.__import__

        def custom_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "yaml" and fromlist:
                if "CSafeLoader" in fromlist or "CSafeDumper" in fromlist:
                    raise ImportError("Cannot import C extensions")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=custom_import):
            # Reload yaml_helper to trigger the ImportError logic
            import keel.yaml_helper

            reload(keel.yaml_helper)

            self.assertIs(keel.yaml_helper._SafeLoader, yaml.SafeLoader)
            self.assertIs(keel.yaml_helper._SafeDumper, yaml.SafeDumper)

            # Test basic functionality under pure python fallback
            data = "foo: bar"
            parsed = keel.yaml_helper.load(data)
            self.assertEqual(parsed, {"foo": "bar"})

            dumped = keel.yaml_helper.dump(parsed)
            self.assertIn("foo: bar", dumped)

        # Restore the module state for subsequent tests
        reload(keel.yaml_helper)


if __name__ == "__main__":
    unittest.main()
