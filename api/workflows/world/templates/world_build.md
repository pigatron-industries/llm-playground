You are generating a new location for a text adventure. 
The player is moving {direction} from "{current_location_name}" into unexplored space.

Surrounding context (for tone/theme consistency — do not contradict):

## World
{world_summary}

## Previous Location: {location_name} ({location_id})
Description: {location_description}
State: {location_state}

## Other known nearby locations
{nearby_locations_summary}

## Available space
Choose a footprint for the location that doesn't overlap any already created locations. The following map show the available space:
{nearby_map}
{available_coords}

Generate a location consistent with the surrounding area and world tone.