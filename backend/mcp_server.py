from mcp.server import MCPServer

from app.settings import DEVELOP_RANGES, validate_develop_settings as validate_settings


mcp = MCPServer("ailr-lightroom")


@mcp.tool()
def list_develop_controls() -> dict[str, dict[str, float]]:
    """List supported Lightroom Classic develop controls and their numeric limits."""
    return {name: {"min": limits[0], "max": limits[1]} for name, limits in DEVELOP_RANGES.items()}


@mcp.tool()
def validate_develop_settings(settings: dict[str, float]) -> dict[str, float]:
    """Validate a proposed set of Lightroom Classic develop values before export."""
    return validate_settings(settings)


if __name__ == "__main__":
    mcp.run(transport="stdio")
