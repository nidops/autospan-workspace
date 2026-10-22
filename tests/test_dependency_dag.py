"""Shared Git dependencies are resolved once, in deterministic postorder."""

from collections import Counter

from aworkspace import plugins


def test_layered_diamond_graph_is_linear_and_ordered():
    width = 32
    visits = Counter()

    class Repository(dict):
        def get(self, key, default=None):
            if key == "depends":
                visits[self["url"]] += 1
                # One traversal plus catalog selection. Fail early if shared
                # subgraphs are expanded repeatedly, without a timing threshold.
                assert visits[self["url"]] <= 2
            return super().get(key, default)

    repos = {
        f"r{i}": Repository(
            {
                "url": f"https://example.org/r{i}.git",
                "revision": "main",
                "python": {},
                "depends": [f"r{i - 1}", f"r{i - 2}"] if i >= 2 else [],
            }
        )
        for i in range(width)
    }
    doc = {"schema": 1, "repositories": repos}
    sources = plugins.plugin_sources(doc, f"r{width - 1}")
    names = [source["name"] for source in sources]
    assert names == ["r1", "r0", *(f"r{i}" for i in range(2, width))]
    assert len(names) == len(set(names)) == width
    assert sum(visits.values()) == 2 * width


def test_shared_dependency_is_selected_before_each_consumer():
    repos = {
        name: {
            "url": f"https://example.org/{name}.git",
            "revision": "master",
            "python": {},
            "depends": dependencies,
        }
        for name, dependencies in {
            "base": [],
            "left": ["base"],
            "right": ["base"],
            "root": ["left", "right"],
        }.items()
    }
    sources = plugins.plugin_sources({"repositories": repos}, "root")
    assert [item["name"] for item in sources] == ["base", "left", "right", "root"]
