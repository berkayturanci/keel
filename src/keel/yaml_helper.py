import yaml

# Use PyYAML C-extensions for ~10x faster parsing/dumping if available.
try:
    from yaml import CSafeDumper as SafeDumper
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeDumper, SafeLoader

def load(stream):
    return yaml.load(stream, Loader=SafeLoader)

def dump(data, stream=None, **kwargs):
    return yaml.dump(data, stream=stream, Dumper=SafeDumper, **kwargs)
