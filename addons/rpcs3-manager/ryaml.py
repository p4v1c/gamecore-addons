"""String-preserving YAML for RPCS3 files.

RPCS3 configs/patches are full of scalars that YAML 1.1 would silently
mangle through a naive PyYAML round-trip:
  - `Aspect ratio: 16:9`  → sexagesimal int 969
  - version keys `01.10:` → float 1.1
So every scalar is loaded as its exact source string (no implicit int/
float/bool/null resolution). On dump, PyYAML's emitter quotes anything
that would otherwise be re-resolved ('01.10', 'true', '100'), and
RPCS3's yaml-cpp reads scalar text identically with or without quotes.
"""
import yaml


class _StrLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        # Real-world RPCS3 patch files redefine anchors (several patch
        # sections each define e.g. &32_9_value). yaml-cpp allows that —
        # last definition wins — but PyYAML raises. Drop the previous
        # definition so redefinition behaves like yaml-cpp.
        event = self.peek_event()
        if (
            not isinstance(event, yaml.events.AliasEvent)  # a *reference* must still resolve
            and getattr(event, "anchor", None) in self.anchors
        ):
            del self.anchors[event.anchor]
        return super().compose_node(parent, index)


# Drop all implicit resolvers: scalars stay plain strings.
_StrLoader.yaml_implicit_resolvers = {}


class _StrDumper(yaml.SafeDumper):
    pass


def load(text: str):
    return yaml.load(text, Loader=_StrLoader)


def dump(data) -> str:
    return yaml.dump(
        data,
        Dumper=_StrDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # never wrap long scalars
    )
