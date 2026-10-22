def install_order(packages, targets):
    """Return a dependencies-first install order for targets."""
    order = []
    done = set()
    path = []

    def visit(name):
        if name in done:
            return
        if name not in packages:
            raise ValueError(f"missing package: {name}")
        if name in path:
            raise ValueError("cycle: " + " -> ".join(path[path.index(name):] + [name]))
        path.append(name)
        for dep in packages[name]:
            visit(dep)
        path.pop()
        done.add(name)
        order.append(name)

    for target in targets:
        visit(target)
    return order
