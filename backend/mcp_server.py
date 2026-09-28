from mcp.server import MCPServer

from app.settings import (
    DEVELOP_RANGES,
    develop_controls_payload,
    validate_develop_settings as validate_settings,
)


mcp = MCPServer("ailr-lightroom")


@mcp.tool()
def list_develop_controls() -> dict[str, object]:
    """List every Lightroom Classic develop control aiLr drives, grouped by panel."""
    return develop_controls_payload()


@mcp.tool()
def list_develop_ranges() -> dict[str, dict[str, float]]:
    """Numeric slider bounds for the develop controls that take numbers."""
    return {name: {"min": limits[0], "max": limits[1]} for name, limits in DEVELOP_RANGES.items()}


@mcp.tool()
def validate_develop_settings(settings: dict[str, object]) -> dict[str, object]:
    """Validate a proposed set of Lightroom Classic develop values before export."""
    return validate_settings(settings)


if __name__ == "__main__":
    mcp.run(transport="stdio")
