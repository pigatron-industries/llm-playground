"""World-specific tools for the world explorer workflow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ...tools.registry import register_tool


class InspectLocationArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    location_id: str = Field(description="The ID of the location to inspect.")

@register_tool(InspectLocationArgs, description="Inspect a specific location in the world.", category="World")
def inspect_location(location_id: str) -> str:
    return f"Inspecting location {location_id}. (World tool not implemented yet.)"



class ListLocationsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


@register_tool(ListLocationsArgs, description="List known locations in the world.", category="World")
def list_locations(world_id: str) -> str:
    return f"Listing locations for the world. (World tool not implemented yet.)"
