"""Small tree reads for register paths and parent choices, not historical rollups."""


def area_paths(areas):
    by_id = {area.pk: area for area in areas}
    paths = {}
    for area in areas:
        names, seen = [], set()
        current = area
        while current is not None and current.pk not in seen:
            seen.add(current.pk)
            names.append(current.name)
            current = by_id.get(current.parent_id)
        paths[area.pk] = " / ".join(reversed(names))
    return paths


def descendant_ids(areas, pk):
    children = {}
    for area in areas:
        children.setdefault(area.parent_id, []).append(area.pk)
    found, pending = {pk}, [pk]
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found
