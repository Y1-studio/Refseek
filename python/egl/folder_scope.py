from pathlib import Path


class FolderScope:
    """A direct folder or a set of hierarchical include/skip rules."""

    def __init__(self, include=(), exclude=(), only=None):
        self.include = tuple(Path(path) for path in include or ())
        self.exclude = tuple(Path(path) for path in exclude or ())
        self.only = Path(only) if only else None

    @property
    def candidate_roots(self):
        return [self.only] if self.only else self.include

    def allows(self, filename):
        path = Path(filename)
        if self.only is not None:
            return path.parent == self.only
        if not self.include and not self.exclude:
            return True
        included = max((len(root.parts) for root in self.include if path.is_relative_to(root)), default=-1)
        excluded = max((len(root.parts) for root in self.exclude if path.is_relative_to(root)), default=-1)
        return included > excluded or (not self.include and excluded == -1)


def folder_tree(roots, records):
    roots = {Path(root) for root in roots}
    counts = dict.fromkeys(roots, 0)
    for filename in {row['path'] for row in records}:
        parents = list(Path(filename).parents)
        root_level = next((i for i in range(len(parents) - 1, -1, -1) if parents[i] in roots), -1)
        for path in parents[:root_level + 1]:
            counts[path] = counts.get(path, 0) + 1
    names = {path: str(path) for path in counts}
    return [dict(path=names[path], label=path.name or names[path], files=counts[path],
                 parent=next((names[parent] for parent in path.parents if parent in names), None))
            for path in sorted(counts, key=lambda path: str(path).casefold())]
