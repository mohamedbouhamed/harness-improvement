def install_order(packages, targets):
    """Return a dependencies-first install order for targets."""
    order = []
    path = []

    def visit(name):
        if name in path:
            raise ValueError(f"cycle detected at {name}")
        if name in order:
            return
        path.append(name)
        for dep in packages.get(name, []):
            visit(dep)
        order.append(name)

    for target in targets:
        visit(target)
    return order
