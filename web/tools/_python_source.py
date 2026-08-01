"""Python 源码静态读取工具"""

import ast
import os


def read_dict_assignment(file_path: str, variable_name: str) -> dict | None:
    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        for node in ast.iter_child_nodes(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == variable_name
            ):
                value = ast.literal_eval(node.value)
                return value if isinstance(value, dict) else None
    except (OSError, SyntaxError, TypeError, ValueError):
        pass
    return None
