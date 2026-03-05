"""
routes — Flask Blueprint package for SEPA-StockLab.

Each sub-module defines a Flask Blueprint that groups related routes.
All blueprints are registered onto the main ``app`` via :func:`register_blueprints`.
"""

from flask import Flask


def register_blueprints(app: Flask) -> None:
    """Import and register every blueprint onto *app*."""
    from .pages         import bp as pages_bp
    from .scan_api      import bp as scan_bp
    from .analyze_api   import bp as analyze_bp
    from .chart_api     import bp as chart_bp
    from .portfolio_api import bp as portfolio_bp
    from .market_api    import bp as market_bp
    from .settings_api  import bp as settings_bp
    from .backtest_api  import bp as backtest_bp
    from .ibkr_api      import bp as ibkr_bp
    from .auto_trade_api import bp as auto_trade_bp

    for blueprint in (
        pages_bp, scan_bp, analyze_bp, chart_bp,
        portfolio_bp, market_bp, settings_bp, backtest_bp, ibkr_bp,
        auto_trade_bp,
    ):
        app.register_blueprint(blueprint)
