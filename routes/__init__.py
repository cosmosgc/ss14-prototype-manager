import importlib
import pkgutil
from flask import Blueprint


def register_blueprints(app):
    """Automatically discover and register blueprints in this package."""

    package_name = __name__  # "routes"

    for _, module_name, _ in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{package_name}.{module_name}")

        # Scan module attributes for Blueprint instances
        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            if isinstance(attr, Blueprint):
                app.register_blueprint(
                    attr,
                    url_prefix=attr.url_prefix or "",
                )
    print([rule.rule for rule in app.url_map.iter_rules()])