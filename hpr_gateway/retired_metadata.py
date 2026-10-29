"""One-off source migration for the separately installed legacy health observer."""
import ast
import copy


def strip_health_discovery(raw):
    """Remove exactly one metadata-only method and its startup invocation."""
    tree = ast.parse(raw)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HealthAgent']
    if len(classes) != 1:
        raise ValueError('Unknown health observer structure')
    owner = classes[0]
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'publish_discovery']
    runners = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == 'run_forever']
    if len(methods) != 1 or len(runners) != 1 or methods[0].decorator_list:
        raise ValueError('Unknown metadata method structure')
    calls = [node for node in runners[0].body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
             and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == 'self'
             and node.value.func.attr == 'publish_discovery']
    if len(calls) != 1 or calls[0].value.args or calls[0].value.keywords:
        raise ValueError('Unknown metadata startup call')
    removed = set()
    for node in (methods[0], calls[0]):
        removed.update(range(node.lineno - 1, node.end_lineno))
    lines = raw.splitlines(keepends=True)
    result = b''.join(line for index, line in enumerate(lines) if index not in removed)
    # Structural equality proves all other native/remediation code is unchanged.
    expected = copy.deepcopy(tree)
    new_owner = next(node for node in expected.body if isinstance(node, ast.ClassDef) and node.name == 'HealthAgent')
    new_owner.body = [node for node in new_owner.body if not (
        isinstance(node, ast.FunctionDef) and node.name == 'publish_discovery')]
    runner = next(node for node in new_owner.body if isinstance(node, ast.FunctionDef) and node.name == 'run_forever')
    runner.body = [node for node in runner.body if node.lineno != calls[0].lineno]
    if ast.dump(ast.parse(result), include_attributes=False) != ast.dump(expected, include_attributes=False):
        raise ValueError('Unintended source change')
    compile(result, '<native-health-observer>', 'exec')
    return result
